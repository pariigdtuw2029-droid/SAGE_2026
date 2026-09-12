ASTRA-GUARD — Scroll Experience
================================

HOW TO VIEW
  Open index.html directly in a browser (double-click it), or serve
  the folder with any static file server. No build step needed.

STRUCTURE
  index.html    - markup
  styles.css    - all styling
  script.js     - scroll -> frame-sequence engine
  assets/rocket/frame-001.jpg ... frame-240.jpg      (system stage)
  assets/satellite/frame-001.jpg ... frame-241.jpg   (subsystem stage)
  assets/component/frame-001.jpg ... frame-241.jpg   (component + defect stage —
                                                       this single sequence carries
                                                       both beats since it already
                                                       dismantles down to the
                                                       red-highlighted fault)

NOTES
  - All ~722 frames preload before scroll is enabled, so fast scrubbing
    never shows a blank or broken frame.
  - Scroll position maps deterministically to frame index, so scrolling
    up reproduces the exact same frames as scrolling down.
  - To add a fourth product sequence later: drop its frames in
    assets/<name>/frame-XXX.jpg, add an entry to SEQUENCES in script.js,
    and extend the segment plan the same way rocket/satellite/component
    are chained together.
