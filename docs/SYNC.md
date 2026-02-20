# Syncing and Pairing Logic

BookWorm Whisper provides bi-directional reading progress synchronization between **Komga** (ebook reader) and **Audiobookshelf** (audiobook player). This allows you to seamlessly switch between reading and listening while maintaining your position.

## Overview

The sync system:
1. Pairs books between services by matching titles
2. Tracks reading/listening progress from both services
3. Determines which service is "ahead" based on chapter comparison
4. Updates the "behind" service to match the "ahead" service

## Configuration

### Environment Variables

| Variable | Description |
|----------|-------------|
| `KOMGA_URL` | Base URL of your Komga server (e.g., `http://localhost:25600`) |
| `KOMGA_API_KEY` | API key for Komga authentication |
| `AUDIOBOOKSHELF_URL` | Base URL of your Audiobookshelf server (e.g., `http://localhost:13378`) |
| `AUDIOBOOKSHELF_API_KEY` | API key for Audiobookshelf authentication |
| `GLOBAL_SYNC_TIME` | Background sync interval in seconds (0 to disable, default: 300) |

## Book Pairing

Books are matched between Komga and Audiobookshelf using **title normalization**:

1. Remove file extensions (`.epub`)
2. Remove content after `--` (often used for author names)
3. Replace underscores and hyphens with spaces
4. Remove parenthetical content
5. Remove quotation marks (including Unicode curly quotes)
6. Normalize whitespace
7. Convert to lowercase

**Example:**
- Komga: `"The_Life_and_Adventures_of_Robinson_Crusoe--Daniel_Defoe.epub"`
- Audiobookshelf: `"The Life and Adventures of Robinson Crusoe"`
- Both normalize to: `"the life and adventures of robinson crusoe"`

## Progress Tracking

### Komga (EPUBs)

For EPUB books, Komga uses the **Readium Web Publication Manifest** format:
- Progress is tracked via the `/api/v1/books/{id}/progression` endpoint
- Chapter information comes from the EPUB's Table of Contents (TOC)
- The current chapter is determined by matching the reading position's `href` to TOC entries

### Audiobookshelf

- Progress is tracked via listening position (time in seconds)
- Chapters are defined by the audiobook's chapter markers
- Current chapter is determined by which chapter's time range contains the current position

## Sync Logic

### Determining Who is Ahead

The sync system compares chapters using **chapter names**, not service-specific chapter numbers. This is important because:
- Komga and Audiobookshelf may have different chapter counts
- Audiobooks often have an intro track that doesn't exist in the ebook
- Chapter numbering may start at different points

**Chapter comparison process:**

1. Extract chapter numbers from names (e.g., "Chapter 5: The Storm" → 5)
2. Support both Arabic (1, 2, 3) and Roman numerals (I, II, III)
3. If no numbers found, compare normalized chapter names
4. Use cross-service name matching as fallback

### Sync Actions

| Scenario | Action |
|----------|--------|
| Same chapter | No sync needed |
| Komga ahead | Update Audiobookshelf to Komga's chapter |
| Audiobookshelf ahead | Update Komga to Audiobookshelf's chapter |
| Cannot determine | Log warning, no sync |

### Chapter Name Matching

When syncing, the system finds the equivalent chapter in the target service:

1. **Exact match**: Normalized chapter names are identical
2. **Chapter number match**: Extracted chapter numbers match
3. **Partial match**: Key parts match (ignoring subtitles after `:` or `.`)
4. **Position fallback**: If chapter counts are similar (within 1.5x ratio), use position

## Enabling Sync

Sync must be explicitly enabled per-book:

1. Book must exist in both Komga and Audiobookshelf (auto-detected)
2. When both are found, `sync_available` is set to `true`
3. User must enable `sync_enabled` via the UI checkbox
4. Only books with `sync_enabled: true` participate in bi-directional sync

## Sync Triggers

### Manual Sync
- Click "Sync All" button in the UI
- Triggers immediate sync for all enabled books

### Background Sync
- Runs automatically every `GLOBAL_SYNC_TIME` seconds (if > 0)
- Performs full data refresh from both services
- Syncs all enabled books

### On Page Load
- Progress data is refreshed from Komga
- UI reflects current sync status

## Metadata Storage

Each book has a JSON metadata file stored alongside the EPUB:

```json
{
  "book_name": "Example Book",
  "source_file": "Example_Book.epub",
  "sync_available": true,
  "sync_enabled": true,
  "reading_progress": {
    "komga": {
      "current_chapter": "Chapter 5: The Storm",
      "current_chapter_num": 5,
      "num_chapters": 20,
      "current_page": 142,
      "total_pages": 450,
      "status": "Reading",
      "found": true
    },
    "audiobookshelf": {
      "current_chapter": "Chapter 5 The Storm",
      "current_chapter_num": 6,
      "num_chapters": 21,
      "current_position": 7234.5,
      "total_duration": 28800,
      "progress_percent": 25.1,
      "status": "Listening",
      "found": true
    }
  }
}
```

## API Endpoints Used

### Komga
| Endpoint | Purpose |
|----------|---------|
| `GET /api/v1/books` | List all books |
| `GET /api/v1/books/{id}/manifest` | Get TOC and chapter info |
| `GET /api/v1/books/{id}/progression` | Get Readium reading position |
| `PUT /api/v1/books/{id}/progression` | Set Readium reading position |
| `GET /api/v1/books/{id}/positions` | Get all reading positions |
| `POST /api/v1/libraries/{id}/scan` | Trigger library scan |

### Audiobookshelf
| Endpoint | Purpose |
|----------|---------|
| `GET /api/libraries` | List all libraries |
| `GET /api/libraries/{id}/items` | List audiobooks in library |
| `GET /api/items/{id}?expanded=1` | Get audiobook with chapters |
| `GET /api/me/progress/{id}` | Get listening progress |
| `PATCH /api/me/progress/{id}` | Update listening progress |
| `POST /api/libraries/{id}/scan` | Trigger library scan |

## Known Limitations

### Divina/Readium Compatibility (Komga)

Komga uses the **Readium Web Publication Manifest** (Divina) format for tracking reading progress in EPUBs. However, not all EPUBs support this format.

**Incompatible EPUBs will show:**
- "Incompatible" status in the Komga progress column
- "Not Divina compatible" message
- Sync checkbox disabled

**Common causes of incompatibility:**
- EPUBs with non-standard internal structure
- EPUBs without proper Table of Contents (TOC/NCX)
- Fixed-layout EPUBs (designed for specific screen sizes)
- Some DRM-free conversions from other formats

**Workarounds:**
- **Try the TOC fix tool**: Run `python tools/fix_epub_toc.py your_book.epub` to rebuild the Table of Contents. This scans for chapter headings and creates proper navigation documents. Note: this doesn't always work depending on the EPUB structure.
- Re-convert the source material to EPUB using Calibre with standard settings
- Use a different EPUB source
- For these books, you'll need to manually track progress between services

**Using the TOC fix tool:**
```bash
# Basic usage - creates your_book_fixed.epub
python tools/fix_epub_toc.py books/your_book.epub

# Specify output path
python tools/fix_epub_toc.py books/your_book.epub books/your_book_repaired.epub
```

The tool will:
1. Extract the EPUB
2. Scan content files for chapter headings (h1-h4 tags, elements with "chapter" class, etc.)
3. Create a new nav.xhtml (EPUB 3) and toc.ncx (EPUB 2) with the found chapters
4. Update the OPF manifest
5. Repackage the EPUB

If no chapter headings are found, it falls back to creating generic "Chapter 1", "Chapter 2", etc. from the spine items.

### Chapter Count Differences

Audiobooks and ebooks often have different chapter structures:
- Audiobooks may have intro/outro tracks not in the ebook
- Some audiobooks split long chapters into multiple tracks
- Chapter numbering may start at different points

BookWorm Whisper handles this by matching **chapter names** rather than chapter numbers, but significant structural differences may cause sync issues.

### Books with No Chapter Names

If chapters are named generically (e.g., "Track 01", "Part 1") without descriptive titles, the sync system may have difficulty matching chapters between services. In these cases:
- Position-based fallback is used if chapter counts are similar
- Manual verification of sync accuracy is recommended

## Troubleshooting

### Books not pairing
- Check that titles normalize to the same value
- Verify both services are configured and accessible
- Check logs for matching attempts

### Sync not working
- Ensure `sync_enabled` is checked in the UI
- Verify `sync_available` is true (book found in both services)
- Check that chapter names can be matched between services

### Wrong chapter after sync
- Chapter counts may differ significantly between services
- Check if audiobook has intro/outro tracks not in ebook
- Review chapter name normalization in logs (DEBUG level)

### Debug logging
Set log level to DEBUG to see detailed sync information:
```
[SYNC] Starting sync for: Example Book
[SYNC] K #5/20 'Chapter 5: The Storm' | ABS #6/21 'Chapter 5 The Storm'
[SYNC] Both at chapter 5 - no sync needed
```
