// setFile function removed as we now use separate pages for conversion

let pollInterval = null;

function setBlur(blur) {
  // Only blur the conversion form if it exists on the current page
  // NEVER blur the main library page
  const convertForm = document.getElementById('convert-form');
  
  if (convertForm) {
    // We're on a conversion page, blur only the form
    if (blur) {
      convertForm.classList.add('blurred');
    } else {
      convertForm.classList.remove('blurred');
    }
  }
  // If we're on the main page, do nothing - don't blur anything
}

function updateProgress(data) {
  const section = document.getElementById('progress-section');
  const filename = document.getElementById('progress-filename');
  const output = document.getElementById('progress-output');
  const errorMsg = document.getElementById('error-message');
  const progressBar = document.getElementById('progress-bar');
  const dismissButton = document.getElementById('dismiss-button');
  const stopButton = document.querySelector('.stop-button');

  if (!section) return;

  if (data.status === 'idle') {
    section.classList.remove('active', 'error', 'completed');
    setBlur(false);
    enableAllActionButtons();
    if (dismissButton) dismissButton.style.display = 'none';
    if (stopButton) stopButton.style.display = 'block';
    if (pollInterval) {
      clearInterval(pollInterval);
      pollInterval = null;
    }
    return;
  }

  section.classList.add('active');
  filename.textContent = data.filename || '';
  output.textContent = data.output ? data.output.join('\n') : '';
  output.scrollTop = output.scrollHeight;

  // Animate progress bar (indeterminate since we don't know exact progress)
  if (data.status === 'running') {
    section.classList.remove('error', 'completed');
    progressBar.style.width = '100%';
    setBlur(true);
    disableAllActionButtons();
    errorMsg.textContent = '';
  } else if (data.status === 'completed') {
    section.classList.remove('error');
    section.classList.add('completed');
    progressBar.style.width = '100%';
    progressBar.style.background = '#10b981';
    setBlur(false);
    enableAllActionButtons();
    errorMsg.textContent = '';
    document.querySelector('.progress-title').textContent = 'Completed: ' + data.filename;
    if (stopButton) stopButton.style.display = 'none';
    if (dismissButton) dismissButton.style.display = 'block';
  } else if (data.status === 'error' || data.status === 'stopped') {
    section.classList.remove('completed');
    section.classList.add('error');
    if (progressBar) {
      progressBar.style.width = '100%';
      progressBar.style.background = '#ef4444';
    }
    setBlur(false);
    enableAllActionButtons();
    if (errorMsg) errorMsg.textContent = data.error || 'An error occurred';
    document.querySelector('.progress-title').textContent =
      (data.status === 'stopped' ? 'Stopped: ' : 'Error: ') + data.filename;
    if (stopButton) stopButton.style.display = 'none';
    if (dismissButton) dismissButton.style.display = 'block';
  }

  if (data.status !== 'running' && pollInterval) {
    clearInterval(pollInterval);
    pollInterval = null;
  }
}

function disableAllActionButtons() {
  // Disable all action buttons (Convert buttons)
  const actionButtons = document.querySelectorAll('.action-button');
  actionButtons.forEach(button => {
    button.disabled = true;
    button.classList.add('disabled');
  });
  
  // Disable upload button and file input
  const uploadButton = document.querySelector('.upload-button');
  const fileInput = document.getElementById('file-input');
  if (uploadButton) {
    uploadButton.disabled = true;
    uploadButton.classList.add('disabled');
  }
  if (fileInput) {
    fileInput.disabled = true;
  }
}

function enableAllActionButtons() {
  // Enable all action buttons (Convert buttons)
  const actionButtons = document.querySelectorAll('.action-button');
  actionButtons.forEach(button => {
    button.disabled = false;
    button.classList.remove('disabled');
  });
  
  // Enable upload button and file input
  const uploadButton = document.querySelector('.upload-button');
  const fileInput = document.getElementById('file-input');
  if (uploadButton) {
    uploadButton.disabled = false;
    uploadButton.classList.remove('disabled');
  }
  if (fileInput) {
    fileInput.disabled = false;
  }
}

function checkStatus() {
  fetch('/status')
    .then(response => response.json())
    .then(data => updateProgress(data))
    .catch(err => console.error('Status check failed:', err));
}

function stopProcess() {
  fetch('/stop', { method: 'POST' })
    .then(response => response.json())
    .then(data => {
      if (data.success) {
        checkStatus();
      }
    })
    .catch(err => console.error('Stop failed:', err));
}

function startPolling() {
  if (!pollInterval) {
    pollInterval = setInterval(checkStatus, 1000);
  }
}

// Check status on page load
document.addEventListener('DOMContentLoaded', function() {
  checkStatus();
  // Start polling if a process might be running
  fetch('/status')
    .then(response => response.json())
    .then(data => {
      if (data.status === 'running') {
        startPolling();
      }
    });
});

// Start polling when form is submitted
const convertForm = document.getElementById('convert-form');
if (convertForm) {
  convertForm.addEventListener('submit', function() {
    setTimeout(startPolling, 500);
  });
}

function dismissProgress() {
  fetch('/dismiss', { method: 'POST' })
    .then(response => response.json())
    .then(data => {
      if (data.success) {
        const section = document.getElementById('progress-section');
        section.classList.remove('active', 'error', 'completed');

        // Reset progress display
        document.getElementById('progress-filename').textContent = '';
        document.getElementById('progress-output').textContent = '';
        document.getElementById('error-message').textContent = '';
        document.getElementById('progress-bar').style.width = '0%';
        document.querySelector('.progress-title').textContent = 'Converting: ';
        document.querySelector('.stop-button').style.display = 'block';
        document.getElementById('dismiss-button').style.display = 'none';
      }
    })
    .catch(err => console.error('Dismiss failed:', err));
}

// Manual sync function - calls bi-directional sync
function syncAll() {
  const syncBtn = document.querySelector('.sync-button');
  if (syncBtn) {
    syncBtn.textContent = '⏳ Syncing...';
    syncBtn.style.pointerEvents = 'none';
  }

  fetch('/api/sync/bidirectional', { method: 'POST' })
    .then(response => response.json())
    .then(data => {
      if (data.success) {
        const updated = data.updated_count || 0;
        console.log(`Bi-directional sync: ${updated} books updated`);
        // Reload the page to show updated data
        window.location.reload();
      } else {
        console.error('Sync failed:', data.error);
        if (syncBtn) {
          syncBtn.textContent = '🔄 Sync All';
          syncBtn.style.pointerEvents = '';
        }
        alert('Sync failed: ' + (data.error || 'Unknown error'));
      }
    })
    .catch(err => {
      console.error('Sync all failed:', err);
      if (syncBtn) {
        syncBtn.textContent = '🔄 Sync All';
        syncBtn.style.pointerEvents = '';
      }
      alert('Sync failed: Network error');
    });
}

// Fetch Komga progress for a specific file
function fetchKomgaProgress(filename, row) {
  const chapterEl = row.querySelector('.komga-chapter');
  const pagesEl = row.querySelector('.komga-pages');
  const progressBar = row.querySelector('.komga-progress-bar');

  fetch(`/api/komga/progress/${encodeURIComponent(filename)}`)
    .then(response => response.json())
    .then(data => {
      const errorType = data.error;

      // Handle error or unavailable status
      if (errorType === 'not_configured' || errorType === 'unavailable') {
        if (chapterEl) {
          chapterEl.textContent = 'Not configured';
          chapterEl.title = 'Komga not configured';
          chapterEl.style.color = '#9ca3af';
        }
        if (pagesEl) {
          pagesEl.textContent = '-/-';
          pagesEl.style.color = '#9ca3af';
        }
        if (progressBar) {
          progressBar.style.width = '0%';
          progressBar.classList.add('no-data');
        }
        return;
      } else if (errorType === 'not_found') {
        if (chapterEl) {
          chapterEl.textContent = 'Not in Komga';
          chapterEl.title = 'Book not found in Komga library';
          chapterEl.style.color = '#9ca3af';
        }
        if (pagesEl) {
          pagesEl.textContent = '-/-';
          pagesEl.style.color = '#9ca3af';
        }
        if (progressBar) {
          progressBar.style.width = '0%';
          progressBar.classList.add('no-data');
        }
        return;
      }

      // Display chapter or status
      if (chapterEl) {
        let chapterDisplay = data.chapter || (data.status === 'Unread' ? 'Not started' : 'Reading');
        chapterEl.textContent = chapterDisplay;
        if (data.chapter) {
          chapterEl.title = data.chapter;
        }
        chapterEl.style.color = ''; // Reset color
      }

      // Update chapter indicator if present
      const progressIndicator = row.querySelector('.komga-progress-bar')?.closest('.progress-container')?.querySelector('.progress-indicator');
      if (progressIndicator && data.current_chapter_num && data.num_chapters) {
        progressIndicator.textContent = `${data.current_chapter_num}/${data.num_chapters}`;
      }

      // Update progress bar - use chapters if available, otherwise pages
      if (progressBar) {
        let percentage = 0;
        if (data.num_chapters > 0 && data.current_chapter_num > 0) {
          // Use chapter-based progress
          percentage = (data.current_chapter_num / data.num_chapters) * 100;
        } else if (data.pages_total > 0) {
          // Fallback to page-based progress
          percentage = (data.pages_read / data.pages_total) * 100;
        }
        percentage = Math.min(percentage, 100);

        progressBar.style.width = `${percentage}%`;
        progressBar.className = 'progress-bar komga-progress-bar';

        if (data.status === 'Completed' || percentage >= 100) {
          progressBar.classList.add('completed');
        } else if (percentage === 0) {
          progressBar.classList.add('no-data');
        }
      }
    })
    .catch(err => {
      console.error('Komga progress fetch failed:', err);
      if (chapterEl) {
        chapterEl.textContent = 'Error';
        chapterEl.style.color = '#ef4444';
        chapterEl.title = 'Network error when fetching progress';
      }
      if (pagesEl) {
        pagesEl.textContent = '-/-';
        pagesEl.style.color = '#9ca3af';
      }
      if (progressBar) {
        progressBar.style.width = '0%';
        progressBar.className = 'progress-bar komga-progress-bar error';
      }
    });
}

// Fetch Audiobookshelf progress for a specific file
function fetchAudiobookshelfProgress(filename, row) {
  const chapterEl = row.querySelector('.audiobookshelf-chapter');
  const positionEl = row.querySelector('.audiobookshelf-position');
  const progressBar = row.querySelector('.audiobookshelf-progress-bar');

  fetch(`/api/audiobookshelf/progress/${encodeURIComponent(filename)}`)
    .then(response => response.json())
    .then(data => {
      if (data.error || data.status === 'not_found' || data.status === 'unavailable' || data.status === 'no_audiobook') {
        // Show indicator for missing data
        const errorType = data.error || data.status;
        if (chapterEl) {
          if (errorType === 'not_configured' || errorType === 'unavailable') {
            chapterEl.textContent = 'Not configured';
            chapterEl.style.color = '#9ca3af';
          } else if (errorType === 'not_found' || errorType === 'no_audiobook') {
            chapterEl.textContent = 'No audiobook';
            chapterEl.style.color = '#9ca3af';
          } else {
            chapterEl.textContent = 'Unavailable';
            chapterEl.style.color = '#9ca3af';
          }
        }
        if (positionEl) {
          positionEl.textContent = '-/-';
          positionEl.style.color = '#9ca3af';
        }
        if (progressBar) {
          progressBar.style.width = '0%';
          progressBar.classList.add('no-data');
        }
        return;
      }

      // Format time as mm:ss or hh:mm:ss
      function formatTime(seconds) {
        if (!seconds || seconds <= 0) return '0:00';
        const hrs = Math.floor(seconds / 3600);
        const mins = Math.floor((seconds % 3600) / 60);
        const secs = Math.floor(seconds % 60);
        if (hrs > 0) {
          return `${hrs}:${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
        }
        return `${mins}:${secs.toString().padStart(2, '0')}`;
      }

      // Display chapter
      if (chapterEl) {
        let chapterDisplay = data.current_chapter;
        if (!chapterDisplay) {
          if (data.status === 'Listening') {
            chapterDisplay = 'Playing';
          } else if (data.status === 'Unplayed') {
            chapterDisplay = 'Not started';
          } else if (data.status === 'Finished') {
            chapterDisplay = 'Finished';
          } else {
            chapterDisplay = '-';
          }
        }
        chapterEl.textContent = chapterDisplay;
        if (data.current_chapter) {
          chapterEl.title = data.current_chapter;
        }

        // Color based on status
        if (data.status === 'Finished') {
          chapterEl.style.color = '#10b981'; // Green
        } else if (data.status === 'Listening') {
          chapterEl.style.color = ''; // Default
        } else {
          chapterEl.style.color = '#9ca3af'; // Gray for unplayed
        }
      }

      // Update chapter indicator if present
      const progressIndicator = row.querySelector('.audiobookshelf-progress-bar')?.closest('.progress-container')?.querySelector('.progress-indicator');
      if (progressIndicator && data.current_chapter_num && data.num_chapters) {
        progressIndicator.textContent = `${data.current_chapter_num}/${data.num_chapters}`;
      }

      // Update progress bar - use chapters if available
      if (progressBar) {
        let percentage = 0;
        if (data.num_chapters > 0 && data.current_chapter_num > 0) {
          // Use chapter-based progress
          percentage = (data.current_chapter_num / data.num_chapters) * 100;
        } else {
          // Fallback to time-based progress
          percentage = data.progress_percent || 0;
        }
        percentage = Math.min(percentage, 100);

        progressBar.style.width = `${percentage}%`;
        progressBar.className = 'progress-bar audiobookshelf-progress-bar';

        if (data.status === 'Finished' || percentage >= 100) {
          progressBar.classList.add('completed');
        } else if (percentage === 0) {
          progressBar.classList.add('no-data');
        }
      }
    })
    .catch(err => {
      console.error('Audiobookshelf progress fetch failed:', err);
      if (chapterEl) {
        chapterEl.textContent = 'Error';
      }
      if (positionEl) {
        positionEl.textContent = '0:00/0:00';
      }
      if (progressBar) {
        progressBar.style.width = '0%';
        progressBar.className = 'progress-bar audiobookshelf-progress-bar error';
      }
    });
}

// Normalize chapter name for comparison
function normalizeChapterName(name) {
  if (!name) return '';
  return name.toLowerCase()
    .replace(/[:.,'"\-?!\u2018\u2019\u201c\u201d]/g, '')  // Include curly quotes and ?!
    .replace(/\s+/g, ' ')
    .trim();
}

// Extract chapter number from chapter name (e.g., "Chapter 4" -> 4, "CHAPTER 5" -> 5)
function extractChapterNumber(name) {
  if (!name) return null;
  // Match patterns like "Chapter 4", "CHAPTER 5", "Ch. 10", "Ch 3"
  const match = name.match(/(?:chapter|ch\.?)\s*(\d+)/i);
  if (match) return parseInt(match[1], 10);
  // Match leading number like "4. Title" or "4 - Title"
  const leadingMatch = name.match(/^(\d+)[\.\-\s]/);
  if (leadingMatch) return parseInt(leadingMatch[1], 10);
  return null;
}

// Update sync status indicators for a row
function updateSyncStatus(row) {
  const komgaCell = row.querySelector('.progress-cell:nth-child(2)');
  const absCell = row.querySelector('.progress-cell:nth-child(3)');

  if (!komgaCell || !absCell) return;

  // Get chapter names from the chapter title elements
  const komgaChapterEl = komgaCell.querySelector('.komga-chapter');
  const absChapterEl = absCell.querySelector('.audiobookshelf-chapter');

  const komgaChapterName = komgaChapterEl ? komgaChapterEl.textContent : '';
  const absChapterName = absChapterEl ? absChapterEl.textContent : '';

  // Normalize names for comparison
  const komgaNormalized = normalizeChapterName(komgaChapterName);
  const absNormalized = normalizeChapterName(absChapterName);

  // Also get chapter numbers as fallback
  const komgaIndicator = komgaCell.querySelector('.progress-indicator');
  const absIndicator = absCell.querySelector('.progress-indicator');

  let komgaCurrent = 0;
  let komgaTotal = 0;
  let absCurrent = 0;
  let absTotal = 0;

  if (komgaIndicator) {
    const komgaMatch = komgaIndicator.textContent.match(/(\d+)\/(\d+)/);
    if (komgaMatch) {
      komgaCurrent = parseInt(komgaMatch[1], 10);
      komgaTotal = parseInt(komgaMatch[2], 10);
    }
  }

  if (absIndicator) {
    const absMatch = absIndicator.textContent.match(/(\d+)\/(\d+)/);
    if (absMatch) {
      absCurrent = parseInt(absMatch[1], 10);
      absTotal = parseInt(absMatch[2], 10);
    }
  }

  // Check if both are at the end (completed)
  const komgaAtEnd = komgaTotal > 0 && komgaCurrent === komgaTotal;
  const absAtEnd = absTotal > 0 && absCurrent === absTotal;
  const bothAtEnd = komgaAtEnd && absAtEnd;

  // Check if both have data (show colors regardless of sync enabled)
  const komgaHasData = !komgaCell.textContent.includes('No data') && !komgaCell.textContent.includes('Not in Komga');
  const absHasData = !absCell.textContent.includes('No data') && !absCell.textContent.includes('No audiobook');

  let komgaStatus = 'sync-status-unknown';
  let absStatus = 'sync-status-unknown';

  if (komgaHasData && absHasData) {
    // Compare by chapter name first (green if same name)
    const namesMatch = komgaNormalized === absNormalized ||
                       (komgaNormalized && absNormalized && (komgaNormalized.includes(absNormalized) || absNormalized.includes(komgaNormalized)));
    const bothAtZero = komgaCurrent === 0 && absCurrent === 0;

    // Extract chapter numbers from names (e.g., "Chapter 4" -> 4)
    const komgaBookChapter = extractChapterNumber(komgaChapterName);
    const absBookChapter = extractChapterNumber(absChapterName);

    // Use book chapter numbers if available, otherwise fall back to service position
    const komgaCompare = komgaBookChapter !== null ? komgaBookChapter : komgaCurrent;
    const absCompare = absBookChapter !== null ? absBookChapter : absCurrent;

    if (namesMatch || bothAtZero || bothAtEnd) {
      // Same chapter name, both at zero, or both at end = synced
      komgaStatus = 'sync-status-synced';
      absStatus = 'sync-status-synced';
    } else if (komgaBookChapter !== null && absBookChapter !== null && komgaBookChapter === absBookChapter) {
      // Same book chapter number = synced
      komgaStatus = 'sync-status-synced';
      absStatus = 'sync-status-synced';
    } else if (komgaCompare > absCompare) {
      komgaStatus = 'sync-status-ahead';
      absStatus = 'sync-status-behind';
    } else if (komgaCompare < absCompare) {
      komgaStatus = 'sync-status-behind';
      absStatus = 'sync-status-ahead';
    } else {
      // Equal positions but different names - show as synced
      komgaStatus = 'sync-status-synced';
      absStatus = 'sync-status-synced';
    }
  }

  // Only update classes if they changed (prevents flash)
  const statusClasses = ['sync-status-synced', 'sync-status-ahead', 'sync-status-behind', 'sync-status-unknown'];
  if (!komgaCell.classList.contains(komgaStatus)) {
    statusClasses.forEach(cls => komgaCell.classList.remove(cls));
    komgaCell.classList.add(komgaStatus);
  }
  if (!absCell.classList.contains(absStatus)) {
    statusClasses.forEach(cls => absCell.classList.remove(cls));
    absCell.classList.add(absStatus);
  }
}

// Load all external progress data
function loadExternalProgress() {
  const table = document.getElementById('files-table');
  if (!table) return;

  const rows = table.querySelectorAll('tr');
  rows.forEach((row, index) => {
    if (index === 0) return; // Skip header row

    const filenameCell = row.querySelector('.filename-cell');
    if (filenameCell) {
      const filename = filenameCell.getAttribute('title');
      fetchKomgaProgress(filename, row);
      fetchAudiobookshelfProgress(filename, row);

      // Update sync status after data loads
      setTimeout(() => updateSyncStatus(row), 500);
    }
  });
}

// Load external progress on page load
document.addEventListener('DOMContentLoaded', function() {
  loadExternalProgress();
  initSyncCheckboxes();
  
  // Add click handler for sync button
  const syncAllBtn = document.querySelector('a[href="/sync-all"]');
  if (syncAllBtn) {
    syncAllBtn.addEventListener('click', function(e) {
      e.preventDefault();
      syncAll();
    });
  }
});

// Initialize sync checkbox event handlers
function initSyncCheckboxes() {
  const checkboxes = document.querySelectorAll('.sync-checkbox');
  checkboxes.forEach(checkbox => {
    checkbox.addEventListener('change', function() {
      const filename = this.getAttribute('data-file');
      const enabled = this.checked;
      const row = this.closest('tr');

      fetch(`/api/sync/enabled/${encodeURIComponent(filename)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: enabled })
      })
        .then(response => response.json())
        .then(data => {
          if (data.error) {
            console.error('Failed to update sync setting:', data.error);
            // Revert checkbox on error
            this.checked = !enabled;
          } else {
            // Update sync status indicators for this row
            updateSyncStatus(row);
          }
        })
        .catch(err => {
          console.error('Failed to update sync setting:', err);
          // Revert checkbox on error
          this.checked = !enabled;
        });
    });
  });
}

// File upload handling - show filename and upload button when file is selected
function initFileUpload() {
  const fileInput = document.getElementById('file-input');
  const uploadButton = document.getElementById('upload-button');
  const mainText = document.getElementById('upload-main-text');
  const subText = document.getElementById('upload-sub-text');

  if (!fileInput || !uploadButton) return;

  fileInput.addEventListener('change', function() {
    if (this.files && this.files.length > 0) {
      const fileName = this.files[0].name;
      mainText.textContent = fileName;
      subText.textContent = 'Click "Upload Book" to upload';
      uploadButton.style.display = 'block';
    } else {
      mainText.textContent = 'Choose EPUB file or drag and drop';
      subText.textContent = 'Supported format: .epub';
      uploadButton.style.display = 'none';
    }
  });
}

// Initialize file upload on page load
document.addEventListener('DOMContentLoaded', function() {
  initFileUpload();
  initFilenameFilter();
  initShowHiddenToggle();
  initHideButtons();
});

// Initialize filename filter functionality
function initFilenameFilter() {
  const filterInput = document.getElementById('filename-filter');
  const table = document.getElementById('files-table');

  if (!filterInput || !table) return;

  filterInput.addEventListener('input', function() {
    const filterValue = this.value.toLowerCase().trim();
    const rows = table.querySelectorAll('tr');

    rows.forEach((row, index) => {
      // Skip header row
      if (index === 0) return;

      const filenameCell = row.querySelector('.filename-cell');
      if (filenameCell) {
        const filename = filenameCell.getAttribute('title').toLowerCase();
        if (filename.includes(filterValue)) {
          row.style.display = '';
        } else {
          row.style.display = 'none';
        }
      }
    });
  });
}

// Initialize show hidden toggle
function initShowHiddenToggle() {
  const checkbox = document.getElementById('show-hidden-checkbox');
  if (!checkbox) return;

  checkbox.addEventListener('change', function() {
    const showHidden = this.checked;
    const url = new URL(window.location);
    if (showHidden) {
      url.searchParams.set('show_hidden', 'true');
    } else {
      url.searchParams.delete('show_hidden');
    }
    window.location.href = url.toString();
  });
}

// Initialize hide buttons
function initHideButtons() {
  const hideButtons = document.querySelectorAll('.hide-button');
  hideButtons.forEach(button => {
    button.addEventListener('click', function() {
      const filename = this.getAttribute('data-file');
      const currentlyHidden = this.getAttribute('data-hidden') === 'true';
      const newHiddenState = !currentlyHidden;

      fetch(`/api/book/hidden/${encodeURIComponent(filename)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ hidden: newHiddenState })
      })
        .then(response => response.json())
        .then(data => {
          if (data.success) {
            // Update button state
            this.setAttribute('data-hidden', newHiddenState ? 'true' : 'false');
            this.textContent = newHiddenState ? 'Show' : 'Hide';
            this.title = newHiddenState ? 'Show this book' : 'Hide this book';

            if (newHiddenState) {
              this.classList.add('hidden-state');
            } else {
              this.classList.remove('hidden-state');
            }

            // If we just hid a book and show_hidden is not enabled, remove the row
            const showHiddenCheckbox = document.getElementById('show-hidden-checkbox');
            if (newHiddenState && showHiddenCheckbox && !showHiddenCheckbox.checked) {
              const row = this.closest('tr');
              if (row) {
                row.style.transition = 'opacity 0.3s ease';
                row.style.opacity = '0';
                setTimeout(() => row.remove(), 300);
              }
            }
          } else {
            console.error('Failed to update hidden status:', data.error);
            alert('Failed to update hidden status');
          }
        })
        .catch(err => {
          console.error('Failed to update hidden status:', err);
          alert('Failed to update hidden status');
        });
    });
  });
}

