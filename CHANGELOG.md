# Changelog

## [1.1.0] - 2026-10-24

### ⚠️ Breaking: settings.toml format

Three option fields in `[parameters]` are now stored as fixed keys instead of
the English labels shown in the UI:

| Field | 1.0.0 | 1.1.0 |
|---|---|---|
| `encoding_mode` | `"Quality (CQP/CRF)"`, `"Bitrate (VBR)"`, `"Bitrate (CBR)"` | `"QUALITY"`, `"VBR"`, `"CBR"` |
| `encoding_pass` | `"1-Pass"`, `"2-Pass"` | `"ONE_PASS"`, `"TWO_PASS"` |
| `normalize_loudness_mode` | `"1-Pass (Faster)"`, `"2-Pass (Recommended)"` | `"ONE_PASS"`, `"TWO_PASS"` |

- Settings files saved by 1.1.0 cannot be read correctly by 1.0.0. That
  version resets the three fields to their defaults, and in bitrate mode also
  clamps the bitrate to the quality range (e.g. 3000 → 51). Do not open a
  1.1.0 project in 1.0.0.
- Settings files from 1.0.0 still open in 1.1.0. The old labels are converted
  to the new keys, and the file is written in the new format the next time it
  is saved.
- When editing `settings.toml` by hand, use the new keys.

### Changed

- Files larger than 64 MB are now fingerprinted from sampled blocks instead of
  being read in full. For a project whose PDF is larger than 64 MB, the
  `pdf_file_hash` stored by 1.0.0 no longer matches: the first time such a
  project is opened in 1.1.0, "PDF Content Updated" is shown once and the
  thumbnails are regenerated. The project settings are not affected.
- Malformed values in a hand-edited `settings.toml` (wrong types, out-of-range
  numbers, unknown options) are now reset to their defaults with a warning in
  the debug log, instead of causing load errors.

[1.1.0]: https://github.com/yosukey/ssmm/compare/v1.0.0...v1.1.0
