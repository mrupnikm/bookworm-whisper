#!/usr/bin/env python3
"""
Fix EPUB Table of Contents

This script rebuilds the TOC for EPUBs that have missing or broken chapter structure.
It scans the content for chapter headings and creates a proper navigation document.

Usage:
    python fix_epub_toc.py input.epub [output.epub]

If output is not specified, it will create input_fixed.epub
"""

import argparse
import os
import re
import sys
import zipfile
import tempfile
import shutil
from xml.etree import ElementTree as ET
from html.parser import HTMLParser


class ChapterFinder(HTMLParser):
    """HTML parser to find chapter headings in EPUB content."""

    def __init__(self):
        super().__init__()
        self.chapters = []
        self.current_tag = None
        self.current_text = ""
        self.in_heading = False

    def handle_starttag(self, tag, attrs):
        self.current_tag = tag
        if tag in ['h1', 'h2', 'h3', 'h4']:
            self.in_heading = True
            self.current_text = ""
        elif tag in ['p', 'div', 'span']:
            attrs_dict = dict(attrs)
            class_name = attrs_dict.get('class', '').lower()
            id_name = attrs_dict.get('id', '').lower()
            if 'chapter' in class_name or 'chapter' in id_name or 'title' in class_name:
                self.in_heading = True
                self.current_text = ""

    def handle_endtag(self, tag):
        if self.in_heading and tag in ['h1', 'h2', 'h3', 'h4', 'p', 'div', 'span']:
            text = self.current_text.strip()
            if text and self._looks_like_chapter(text):
                self.chapters.append(text)
            self.in_heading = False
            self.current_text = ""
        self.current_tag = None

    def handle_data(self, data):
        if self.in_heading:
            self.current_text += data

    def _looks_like_chapter(self, text):
        """Check if text looks like a chapter heading."""
        text_lower = text.lower().strip()

        patterns = [
            r'^chapter\s+\d+',
            r'^chapter\s+[ivxlc]+',
            r'^ch\.\s*\d+',
            r'^part\s+\d+',
            r'^section\s+\d+',
            r'^\d+\.\s+\w+',
            r'^prologue',
            r'^epilogue',
            r'^introduction',
            r'^preface',
        ]

        for pattern in patterns:
            if re.match(pattern, text_lower):
                return True

        if len(text) < 100 and text[0].isupper():
            if any(word in text_lower for word in ['chapter', 'part', 'book', 'act', 'scene']):
                return True

        return False


def extract_epub(epub_path, extract_dir):
    """Extract EPUB contents to a directory."""
    with zipfile.ZipFile(epub_path, 'r') as zf:
        zf.extractall(extract_dir)


def find_opf_path(extract_dir):
    """Find the OPF file path from container.xml."""
    container_path = os.path.join(extract_dir, 'META-INF', 'container.xml')
    if not os.path.exists(container_path):
        raise ValueError("Invalid EPUB: missing container.xml")

    tree = ET.parse(container_path)
    root = tree.getroot()

    ns = {'container': 'urn:oasis:names:tc:opendocument:xmlns:container'}
    rootfile = root.find('.//container:rootfile', ns)

    if rootfile is None:
        rootfile = root.find('.//{*}rootfile')

    if rootfile is None:
        raise ValueError("Invalid EPUB: cannot find rootfile in container.xml")

    return rootfile.get('full-path')


def parse_opf(opf_path):
    """Parse the OPF file and return spine items."""
    tree = ET.parse(opf_path)
    root = tree.getroot()

    ns_match = re.match(r'\{(.+)\}', root.tag)
    ns = {'opf': ns_match.group(1)} if ns_match else {}

    manifest = {}
    manifest_elem = root.find('.//opf:manifest', ns) if ns else root.find('.//{*}manifest')
    if manifest_elem is None:
        manifest_elem = root.find('.//manifest')

    if manifest_elem is not None:
        for item in manifest_elem:
            item_id = item.get('id')
            href = item.get('href')
            media_type = item.get('media-type', '')
            if item_id and href:
                manifest[item_id] = {'href': href, 'media_type': media_type}

    spine_items = []
    spine_elem = root.find('.//opf:spine', ns) if ns else root.find('.//{*}spine')
    if spine_elem is None:
        spine_elem = root.find('.//spine')

    if spine_elem is not None:
        for itemref in spine_elem:
            idref = itemref.get('idref')
            if idref and idref in manifest:
                spine_items.append({
                    'id': idref,
                    'href': manifest[idref]['href'],
                    'media_type': manifest[idref]['media_type']
                })

    return spine_items, manifest, tree, root, ns


def scan_for_chapters(extract_dir, opf_dir, spine_items):
    """Scan content files for chapter headings."""
    chapters = []

    for i, item in enumerate(spine_items):
        if 'html' not in item['media_type'].lower() and 'xhtml' not in item['media_type'].lower():
            continue

        content_path = os.path.join(opf_dir, item['href'])
        if not os.path.exists(content_path):
            continue

        try:
            with open(content_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception:
            continue

        parser = ChapterFinder()
        try:
            parser.feed(content)
        except Exception:
            continue

        for chapter_title in parser.chapters:
            chapters.append({
                'title': chapter_title,
                'href': item['href'],
                'order': i
            })

        if not parser.chapters:
            chapter_matches = re.findall(
                r'<h[1-4][^>]*>([^<]*(?:chapter|part|prologue|epilogue)[^<]*)</h[1-4]>',
                content,
                re.IGNORECASE
            )
            for match in chapter_matches:
                title = re.sub(r'<[^>]+>', '', match).strip()
                if title:
                    chapters.append({
                        'title': title,
                        'href': item['href'],
                        'order': i
                    })

    seen = set()
    unique_chapters = []
    for ch in chapters:
        key = (ch['title'], ch['href'])
        if key not in seen:
            seen.add(key)
            unique_chapters.append(ch)

    return unique_chapters


def create_nav_xhtml(chapters, opf_dir):
    """Create a new nav.xhtml file with proper TOC."""
    nav_content = '''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head>
    <title>Table of Contents</title>
</head>
<body>
    <nav epub:type="toc" id="toc">
        <h1>Table of Contents</h1>
        <ol>
'''

    for i, chapter in enumerate(chapters, 1):
        title = chapter['title'].replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        href = chapter['href']
        nav_content += f'            <li><a href="{href}">{title}</a></li>\n'

    nav_content += '''        </ol>
    </nav>
</body>
</html>
'''

    nav_path = os.path.join(opf_dir, 'nav.xhtml')
    with open(nav_path, 'w', encoding='utf-8') as f:
        f.write(nav_content)

    return 'nav.xhtml'


def create_ncx(chapters, opf_dir, book_id='book'):
    """Create a toc.ncx file for EPUB 2 compatibility."""
    ncx_content = f'''<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
<head>
    <meta name="dtb:uid" content="{book_id}"/>
    <meta name="dtb:depth" content="1"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
</head>
<docTitle>
    <text>Table of Contents</text>
</docTitle>
<navMap>
'''

    for i, chapter in enumerate(chapters, 1):
        title = chapter['title'].replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        href = chapter['href']
        ncx_content += f'''    <navPoint id="navPoint-{i}" playOrder="{i}">
        <navLabel>
            <text>{title}</text>
        </navLabel>
        <content src="{href}"/>
    </navPoint>
'''

    ncx_content += '''</navMap>
</ncx>
'''

    ncx_path = os.path.join(opf_dir, 'toc.ncx')
    with open(ncx_path, 'w', encoding='utf-8') as f:
        f.write(ncx_content)

    return 'toc.ncx'


def update_opf(opf_path, tree, root, ns, nav_href, ncx_href, manifest):
    """Update OPF file to include new nav and ncx."""
    manifest_elem = root.find('.//opf:manifest', ns) if ns else root.find('.//{*}manifest')
    if manifest_elem is None:
        manifest_elem = root.find('.//manifest')

    if manifest_elem is None:
        raise ValueError("Cannot find manifest in OPF")

    nav_exists = any(item.get('href') == nav_href for item in manifest_elem)
    ncx_exists = any(item.get('href') == ncx_href for item in manifest_elem)

    if not nav_exists:
        nav_item = ET.SubElement(manifest_elem, 'item')
        nav_item.set('id', 'nav')
        nav_item.set('href', nav_href)
        nav_item.set('media-type', 'application/xhtml+xml')
        nav_item.set('properties', 'nav')

    if not ncx_exists:
        ncx_item = ET.SubElement(manifest_elem, 'item')
        ncx_item.set('id', 'ncx')
        ncx_item.set('href', ncx_href)
        ncx_item.set('media-type', 'application/x-dtbncx+xml')

    spine_elem = root.find('.//opf:spine', ns) if ns else root.find('.//{*}spine')
    if spine_elem is None:
        spine_elem = root.find('.//spine')

    if spine_elem is not None:
        spine_elem.set('toc', 'ncx')

    tree.write(opf_path, encoding='utf-8', xml_declaration=True)


def repackage_epub(extract_dir, output_path):
    """Repackage the EPUB from extracted directory."""
    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        mimetype_path = os.path.join(extract_dir, 'mimetype')
        if os.path.exists(mimetype_path):
            zf.write(mimetype_path, 'mimetype', compress_type=zipfile.ZIP_STORED)

        for root, dirs, files in os.walk(extract_dir):
            for file in files:
                if file == 'mimetype':
                    continue
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, extract_dir)
                zf.write(file_path, arcname)


def fix_epub_toc(input_path, output_path=None):
    """Main function to fix EPUB TOC."""
    if not os.path.exists(input_path):
        print(f"Error: Input file not found: {input_path}")
        return False

    if output_path is None:
        base, ext = os.path.splitext(input_path)
        output_path = f"{base}_fixed{ext}"

    print(f"Processing: {input_path}")

    temp_dir = tempfile.mkdtemp(prefix='epub_fix_')

    try:
        print("  Extracting EPUB...")
        extract_epub(input_path, temp_dir)

        print("  Finding OPF file...")
        opf_rel_path = find_opf_path(temp_dir)
        opf_path = os.path.join(temp_dir, opf_rel_path)
        opf_dir = os.path.dirname(opf_path)

        print("  Parsing OPF...")
        spine_items, manifest, tree, root, ns = parse_opf(opf_path)
        print(f"  Found {len(spine_items)} spine items")

        print("  Scanning for chapter headings...")
        chapters = scan_for_chapters(temp_dir, opf_dir, spine_items)

        if not chapters:
            print("  No chapter headings found. Creating chapters from spine items...")
            for i, item in enumerate(spine_items, 1):
                if 'html' in item['media_type'].lower() or 'xhtml' in item['media_type'].lower():
                    chapters.append({
                        'title': f'Chapter {i}',
                        'href': item['href'],
                        'order': i
                    })

        print(f"  Found/created {len(chapters)} chapters")

        if chapters:
            print("  Sample chapters:")
            for ch in chapters[:5]:
                print(f"    - {ch['title']}")
            if len(chapters) > 5:
                print(f"    ... and {len(chapters) - 5} more")

        print("  Creating nav.xhtml...")
        nav_href = create_nav_xhtml(chapters, opf_dir)

        print("  Creating toc.ncx...")
        ncx_href = create_ncx(chapters, opf_dir)

        print("  Updating OPF...")
        update_opf(opf_path, tree, root, ns, nav_href, ncx_href, manifest)

        print("  Repackaging EPUB...")
        repackage_epub(temp_dir, output_path)

        print(f"\nSuccess! Fixed EPUB saved to: {output_path}")
        print(f"Total chapters: {len(chapters)}")
        return True

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return False

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(
        description='Fix EPUB Table of Contents for Komga compatibility'
    )
    parser.add_argument('input', help='Input EPUB file')
    parser.add_argument('output', nargs='?', help='Output EPUB file (default: input_fixed.epub)')

    args = parser.parse_args()

    success = fix_epub_toc(args.input, args.output)
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
