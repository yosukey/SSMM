# Simple Slideshow Movie Maker (SSMM)

![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)

Turn a PDF into a clean, narrated slideshow movie with optional picture-in-picture (PinP) video overlays — no timeline editing required.  
Designed for on-demand seminar, tutorial, and classroom content.

---

## 🎯 Concept

Traditionally, video creation involves a steep learning curve with complex editing tools and significant time invested in timeline-based work. Moreover, the daunting task of re-editing often discourages creators from keeping their materials current, risking that valuable, high-quality content becomes obsolete. This project was born from a desire to shift the focus from the complexities of video editing back to the quality of the content itself. 

Built on this principle, the application automates slideshow video creation by pairing a PDF slide deck with corresponding audio or video files. When a video needs updating, creators can simply modify the source materials—such as correcting the original PowerPoint and exporting a new PDF, or re-recording a specific audio segment. The entire video can then be regenerated with minimal effort. This workflow makes it effortless to maintain and prolong the life of valuable educational content, ensuring it remains current and relevant.

While a video composed only of slide transitions might seem visually simple, the philosophy behind this project is that, much like the traditional Japanese art of Kamishibai (https://en.wikipedia.org/wiki/Kamishibai), the power to engage an audience comes from the narrative—the content and its delivery—not from complex visual effects. This minimalist approach allows the message to take center stage, enabling creators to craft compelling narratives that captivate and inform.

## 👥 Audience

This tool targets **beginner to intermediate** creators making seminar, tutorial, and classroom videos. Advanced users will also find it convenient as a fast, **no‑timeline** workflow for procedural slideshow movies.

---
## ✨ What You Can Do with SSMM

Turn your PDF documents into polished, narrated videos with ease.

**1. Easily Create Videos from Your PDF**

Simply import your PDF file, and each page instantly becomes a slide in your video. No complex setup is required.

**2. Add Voice-overs or a Personal Touch to Each Slide**

For every slide, you can choose how to bring it to life:
* **Silent**: Keep it simple with just the visual slide.
* **Audio File**: Add a pre-recorded narration or audio clip (like an MP3).
* **Video File (Picture-in-Picture)**: Show yourself speaking in a small window on top of your slide. Just add your camera footage.

**3. Use Your GPU for Faster Encoding**

At startup the app tests which encoders actually work on your computer (NVIDIA NVENC, Intel Quick Sync, AMD AMF or Apple VideoToolbox, plus the software encoders) and offers only those. Pick hardware encoding with one click for faster exports, or keep the reliable software encoder.

**4. Get Consistent, Professional Audio**

Optionally normalize the loudness of the finished video to the EBU R128 broadcast standard (-23 LUFS), so videos made from recordings of different levels play back at a consistent volume.

**5. Protect and Brand Your Content with Watermarks**

Easily add a text watermark—like your name or website—to your video. You can fully customize its color, transparency, and rotation to protect and brand your work. This feature is also perfect for managing your workflow. You can temporarily add a watermark like "DRAFT" or "For Review Only" to indicate a video isn't final, which is great for sharing progress with colleagues or clients.

**6. Make Your Videos Easy to Navigate with Chapters**

The app can add chapters for your presentation, allowing viewers to jump to the sections they care about. It also creates a timestamped chapter list that you can copy and paste directly into your YouTube video description, saving you time.

---

## 📦 Setup

Ready-to-run applications are published on the [GitHub Releases](https://github.com/yosukey/SSMM/releases) page:

* **Windows**: installer (`SSMM-<version>-windows-x64-setup.exe`). A portable ZIP (`SSMM-<version>-windows-x64-portable.zip`; extract it and run `SSMM.exe`) may also be provided.
* **macOS (Apple silicon)**: disk image (`SSMM-<version>-macOS-arm64.dmg`).

#### Notes on the Ready-to-Run Application

* **FFmpeg is not included.** SSMM uses the FFmpeg installed on your computer, so that the hardware encoders of your GPU can be used. Install it as described in [Installing FFmpeg](#installing-ffmpeg) or via the app's **Tools → Install FFmpeg...** menu.
* **Security warning on first launch.** The application is not digitally signed, so your operating system will likely show a security warning. You will need to approve it manually to run it.
* **Download size.** The apps are built with PyInstaller in one-folder (one-dir) mode, so Python and all libraries are shipped alongside the executable.

### Running with Python 🐍

Works on Windows, macOS and Linux.

1. **Install Python 3.10 or newer.** On Python 3.12+ see the note below.
2. **Clone this repository**, then install the dependencies:
   ```terminal
   pip install -r requirements.txt
   ```
3. **Install FFmpeg** (see below), then start the app with `python main.py`.

#### Python 3.12+ users

The theme package **PyQtDarkTheme** (`pyqtdarktheme`) declares support only up to Python 3.11, so `pip` may refuse to install it. The app itself works on newer Python versions. Install the package with `--ignore-requires-python` first, then the remaining requirements:

```terminal
pip install pyqtdarktheme==2.1.0 --ignore-requires-python
pip install -r requirements.txt
```

### Installing FFmpeg

SSMM needs a matching pair of `ffmpeg` and `ffprobe`. A full build includes the hardware encoders SSMM can use.

- **Windows**: `winget install Gyan.FFmpeg`, or **Tools → Install FFmpeg...** in the app (runs winget for you).
- **macOS**: `brew install ffmpeg`, or **Tools → Install FFmpeg...** in the app (runs Homebrew for you).
- **Linux**: use your distribution's package manager (e.g. `sudo apt install ffmpeg`) or a static build.

FFmpeg is searched for in this order:

1. `~/ffmpeg-bin` (place `ffmpeg` and `ffprobe` here to use a specific build, e.g. a portable one)
2. the system `PATH`
3. package-manager folders that may be missing from `PATH`: on macOS `/opt/homebrew/bin`, `/usr/local/bin`, `/opt/local/bin` (an app started from Finder does not see your shell's `PATH`); on Windows the winget `Links` folders, so an FFmpeg installed from the running app is found without a restart

The **Validation Result** tab shows which FFmpeg was found and a table of the encoders that passed the startup test.

---

## 🚀 Quick Start

1. **Create a project folder** and put **exactly one PDF** (your slide deck) into it.
2. Put the **audio** and/or **video** files you want to use into the same folder (see [supported formats](#-faq)).
   - Tip: when a folder is opened as a new project, a file whose name starts with a three-digit slide number in brackets, e.g. `[001]intro.mp3` or `[012]demo.mp4`, is assigned to that slide automatically.
3. Start the app (click the app icon, or run `python main.py`) and follow the numbered steps on the left:
   - **1. Select Project Folder**: choose the folder. If it contains a `settings.toml`, the saved project is restored.
   - **2. Select Output Folder**: choose where the video is written (it must not be the project folder).
      > **Important:** Temporary working folders (`movie-temp-*` / `preview-temp-*`) are created in the output folder while a video is being made. Choose a fast local disk with enough free space; avoid network drives and USB flash storage.
   - **3. Set Parameters**: adjust the settings in the **Basic**, **Audio Options**, **Video Options** and **Watermark** tabs, and assign a material to every slide in the **Slide Settings** tab (or mark it `SILENT`).
   - **4. Validation**: click **Check Files**. The **Validation Result** tab summarizes your files and lists any errors, warnings and notices. Optionally, **Create Preview** renders a single slide (with or without its neighbouring intervals) to try your settings.
   - **5. Create Video**: enter a file name (defaults to the project folder name) and click **Create Video**.

Any change to settings, slides or project files after validation requires running **Check Files** again.

### Saving and loading projects

- **File → Save Project Settings...** writes `settings.toml` (by default into the project folder) once the project has been validated successfully. Settings files from older versions can still be loaded.
- **File → Load Project Settings...**, **File → Open Recent** (last five projects), or simply selecting a project folder that contains `settings.toml`, restores a project.
- If the PDF has changed since the settings were saved, SSMM notices it. When only the content changed, thumbnails are refreshed automatically. When pages were added or removed, you can **migrate** the slide settings: the pages of the old and new PDF are matched by image similarity and you can correct the mapping before applying it.

### Importing DougaMeijin projects

You can **import a [DougaMeijin](https://github.com/yosukey/DougaMeijin) project file** (`.dmj`) via **File → Import DougaMeijin Project...**. You choose the folder to extract into, the archive is unpacked into a new subfolder there, its page images are combined into a PDF, and the per-page narration (FLAC) is brought in as audio materials so you can continue editing in SSMM. The recent-projects list then points to the extracted folder. (This is a one-way import; SSMM does not write the `.dmj` format.)

---

## 🧠 How It Works (High-level)

1. **Encoder discovery & tests** — At every startup the app lists the encoders of the detected FFmpeg and *test-encodes* a short clip with `libx264`, `libx265` and each hardware encoder candidate, so only encoders that really work on this machine are offered (`mpeg4` and `libaom-av1` are offered whenever the build contains them).
2. **PDF analysis** — Page count, page sizes, thumbnails and a perceptual hash (p-hash) of every page are computed. Thumbnails and hashes are stored in `settings.toml`, which lets SSMM detect a modified PDF and match old and new pages when the page structure changes.
3. **Media probing** — Every audio/video file in the project folder is probed with ffprobe (duration, audio streams, video size, frame rate, rotation, display aspect ratio, interlacing) and cached for the session. Files are classified by the streams they actually contain, so an `.mp4` without a video stream is treated as audio.
4. **Validation** — Checks the FFmpeg installation, the PDF (exactly one, aspect ratio vs. output resolution), material assignments, the output file name, encoder/parameter compatibility and, if enabled, the YouTube chapter rules. Warnings point out e.g. variable frame rate, upscaled PinP video or black bars.
5. **Export**
   - Each PDF page is rendered at the output width and fitted into the output frame (black bars are added if the aspect ratios differ).
   - One segment per slide: **Silent** (the page held for the given duration), **Audio** (the page with the selected audio stream), or **Video** (the video overlaid as PinP on the page, rotated upright, deinterlaced if needed, with the chosen effects). The **watermark** is burned into every segment.
   - Between slides an **interval** is inserted: either a transition (cross-fade style effects) or, without a transition, the last frame of the previous slide followed by the first frame of the next one.
   - All durations are rounded up to whole frames, so embedded chapters and the YouTube chapter list match the video exactly.
   - Segments are encoded with your codec, encoder, mode and pass settings, with uncompressed (PCM) audio so they join without gaps. They are then concatenated, the audio is encoded to AAC once (with optional loudness normalization), chapters are embedded, and the MP4 is written.
6. While a video is being created, system sleep is suppressed on Windows and macOS.

---

## ⚙️ Parameters

**Basic**
- **Resolution**: 3840x2160, 1920x1080, 1280x720, 960x540, 426x240 (16:9) or 1280x960, 960x720, 640x480 (4:3)
- **FPS**: 60, 30, 24, 10, 5, 3
- **Codec**: H.264, H.265/HEVC, AV1, MPEG-4 Part 2 (only codecs with a working encoder are listed)
- **Hardware Encoding**: `None` (software: `libx264` / `libx265` / `libaom-av1` / `mpeg4`) or a tested hardware encoder (NVIDIA, Intel, AMD, Apple VideoToolbox)

**Video Options**
- **Encoding Mode**: Quality (CRF/CQP; MPEG-4 uses qscale 1-31) / Bitrate VBR / Bitrate CBR. VideoToolbox supports the bitrate modes only.
- **Pass**: 1-Pass / 2-Pass (bitrate modes only). 2-Pass uses FFmpeg's two-pass encoding for the software encoders and NVENC's internal multipass; it is not available for QSV, AMF and VideoToolbox.
- **Chapters**: chapters are embedded into the MP4 whenever a slide has a chapter title. **Export chapter file for YouTube** additionally writes `<video name>-chapter-for-youtube.txt`; YouTube's rules are checked during validation (the first chapter on slide 1, at least three chapters, each at least 10 seconds).

**Audio Options**
- **Bitrate** 96k-256k, **sample rate** 16-48 kHz, **channels** 1-8
- **Normalize Loudness (EBU R128)**: off / 1-Pass (faster, dynamic) / 2-Pass (measured, recommended); target -23 LUFS, LRA 7, true peak -2 dBTP

**Watermark**
- Text, opacity, color, font (bundled Noto Sans families incl. Japanese, Arabic, Devanagari and Thai), size (% of video height), rotation (none / ±45°) and tiling. The watermark is also shown on the slide thumbnails.

**Slide Settings** (one row per PDF page)
- **Material**: an audio/video file, `SILENT` (with a duration of 1-100 s), or unassigned (blocks validation). The ▶ button plays the material (audio with a waveform); double-click a thumbnail to enlarge it.
- **Audio Stream**: choose the stream of files with several audio tracks.
- **Chapter Title**
- **PinP Position** (center or a corner), **PinP Scale** (5-100 % of the video height) and **PinP Effect**: one shape (circle, green chroma key, vignette), one color effect (grayscale, sepia, negative) and any of horizontal/vertical flip, blur and pixelate (chroma key cannot be combined with a color effect, blur or pixelate).
- **Interval to Next** (0-300 s) and **Transition to Next** (30 transitions; see **Tools → Transition Gallery...**). A transition requires an interval of at least 1 s.
- **Edit Selected Slides...** applies interval, transition and PinP settings to many slides at once; the **All / Movie / Audio** buttons select rows quickly.

**Output** (step 5)
- File name (default: the project folder name), **Append Duration to Filename** (e.g. `lecture_12m5s.mp4`), and **Delete Temporary Files** (uncheck to keep the working files for troubleshooting).

> Availability of hardware encoders depends on your machine, its drivers and your FFmpeg build.

## 🔐 Internal Encoding Parameters

To ensure quality and compatibility, a few encoding parameters are fixed and not exposed in the user interface.

### Video Settings

```-pix_fmt yuv420p```

The standard pixel format with the widest playback compatibility.

```-colorspace bt709 (and related parameters)```

The standard color space for HD video, so colors are displayed consistently.

```-g <FPS * 2> (GOP Size)```

A keyframe about every two seconds: a good balance between seeking performance and compression efficiency.

### Profile, Preset & Level

For each encoder a standard profile (`high`, `main`, ...) and preset (`medium`, `p5`, ...) are selected for a good balance between quality and speed. The H.264/HEVC level is chosen by the encoder from the resolution and frame rate. HEVC files are tagged `hvc1` for playback in Apple's players.

### Audio Settings

```-c:a aac```

The final audio track is AAC, the most widely supported audio format for MP4. Intermediate segments use uncompressed PCM, so AAC is encoded only once.

### Container Settings

```-movflags +faststart```

Places the file's index at the beginning, so playback can start while the file is still downloading.

---

## 🖥️ Launch Options

```terminal
python main.py [--verbose] [PROJECT]
```

- `-v`, `--verbose`: start with **Verbose Logging** enabled in the **Debug** tab (FFmpeg command lines and output, state transitions). It can also be toggled there at any time, and **Export to File...** saves the log together with system information (an output folder must be selected).
- `PROJECT` (optional) is loaded on startup:
  - a **folder**: loaded as a project; a `settings.toml` inside it is restored,
  - a **`.toml`** file: the saved project is restored,
  - a **`.dmj`** file: the DougaMeijin project is imported next to the `.dmj` file.

### Appearance and language

- **View → Theme**: System Default / Light / Dark. The UI is styled with [PyQtDarkTheme](https://github.com/5yutan5/PyQtDarkTheme) plus the app's own accent color; the change applies immediately.
- **View → Language**: System Default / English / 日本語 (takes effect after a restart).
- **Help → Check for Updates...** compares your version with the latest GitHub release.

---

## ❓ FAQ

**Q. The app can’t find FFmpeg.**  
A. Install it (see [Installing FFmpeg](#installing-ffmpeg)) or place `ffmpeg` and `ffprobe` into `~/ffmpeg-bin/`, then restart the app. Both executables must come from the same build.

**Q. Hardware encoders don’t show up.**  
A. They depend on your hardware, its drivers and how FFmpeg was built. The **Debug** tab shows why each encoder test failed. Update the graphics driver, try a full FFmpeg build, or use software encoding (`None`).

**Q. What formats can I use?**  
A. The following file types are accepted as materials:

- Video: MP4, MOV, AVI
- Audio: MP3, WAV, FLAC, AAC

These containers can hold audio and video encoded in many different ways, so not every file is guaranteed to work even if its extension is supported. The validation report lists what was detected in each file.

---

## 🛠️ Development

```terminal
pip install -r requirements.txt pytest ruff
ruff check --select F ssmm tests main.py
pytest
```

The tests need `ffmpeg`/`ffprobe` on `PATH` for the FFmpeg-based checks (they are skipped otherwise). On a machine without a display, set `QT_QPA_PLATFORM=offscreen`. The same checks run in GitHub Actions on every push and pull request.

---

## 🙏 Acknowledgments

- FFmpeg and the broader open-source multimedia community
- PySide6 / Qt for Python
- pypdfium2 (PDFium), Pillow (PIL), ImageHash, PyQtDarkTheme, darkdetect

### Third-Party Components & Licenses

SSMM is distributed as a self-contained application that bundles or links the
components below (FFmpeg is not among them; it is installed separately by the user). All of them are permissive licenses, or copyleft licenses
(LGPL) whose obligations are satisfied by dynamic linking together with the
notices and source references provided in the application's "Licenses" tab.
This keeps the distributed application consistent with SSMM's own MIT license.

| Component | Role | License |
| --- | --- | --- |
| PySide6 / Qt for Python (incl. Qt Multimedia) | GUI toolkit, media playback | LGPL-3.0 |
| pypdfium2 (bundles Google PDFium) | PDF rendering & reading | Apache-2.0 / BSD-3-Clause (PDFium: BSD-3-Clause) |
| Pillow | Image processing & image→PDF | HPND (MIT-style) |
| PyQtDarkTheme | Light/dark UI theme | MIT |
| darkdetect | OS dark-mode detection | BSD-3-Clause |
| psutil | System info | BSD-3-Clause |
| ImageHash | Perceptual hashing | BSD-2-Clause |
| NumPy, SciPy, PyWavelets (used by ImageHash) | Numerical computing | BSD-3-Clause, BSD-3-Clause, MIT |
| toml | TOML parsing | MIT |
| packaging | Version handling | Apache-2.0 / BSD-2-Clause |
| Noto Sans fonts (bundled) | Watermark text rendering | SIL Open Font License 1.1 |

FFmpeg is **not** distributed with SSMM; the copy installed by the user is run as a separate program, and its license (LGPL or GPL, depending on the build) applies to that installation.

> Note: SSMM previously used PyMuPDF (AGPL-3.0) for PDF handling. It has been
> replaced by pypdfium2 so that the entire distributed application remains
> under permissive terms compatible with the MIT license.

#### Funding

This work represents an outcome of the research project supported by JSPS KAKENHI Grant Number JP25K15395.

---

## 📄 License

This project is licensed under the MIT License. The full text is in the
[`LICENSE`](LICENSE) file.

**Copyright** (c) 2025-- Yosuke Yamazaki

The MIT license covers SSMM's own source code. Bundled and linked third-party
components keep their own licenses; see
[Third-Party Components & Licenses](#third-party-components--licenses) above and
the "Licenses" tab within the application for the full notices and texts.

### Disclaimer

This software is provided 'as is' without warranty of any kind, express or implied, 
including but not limited to the warranties of merchantability, fitness for a particular purpose, and noninfringement. 
In no event shall the authors or copyright holders be liable for any claim, damages, or other liability, 
whether in an action of contract, tort, or otherwise, arising from, out of, or in connection with the software 
or the use or other dealings in the software.

You use this software at your own risk. The developers assume no responsibility for any loss of data or damage 
to your system that may result from its use. It is highly recommended to back up your data before using this application.

### Content & Publication Disclaimer

This application assumes that you will use only materials for which you hold the rights or for which you have obtained valid permission. You are responsible for ensuring that your use does not violate third-party copyrights (and, where applicable, related/neighboring rights), rights of publicity and portrait rights, trademarks, privacy or other rights, and that you comply with your institution’s rules, contracts, and any platform terms of service.

You are solely responsible for all rights clearances and required notices when publishing or distributing videos created with this app (including credits, source attributions, license notices, and compliance with quotation/fair-use requirements, as applicable). The app provider does not review your materials or workflows and accepts no liability for any resulting loss or damage. If needed, consult a qualified professional. This is not legal advice.

### Font License

This application bundles Noto Sans by Google and Adobe. It is available under the SIL Open Font License (OFL), a free and open source license. You can find the full text of the license in the "Licenses" tab within the application.

---

## 🚗 Roadmap

1. Code sign the distributed binaries.
1. Prepare user manual.

---

## 💝 Support This Research

The app was developed by Dr. Yamazaki at Nihon University School of Dentistry as part of ongoing research in dental education.

This software is freely available for all. If you find it valuable for your work, please consider supporting our continued research.

研究活動を継続するためのご支援として、研究助成金のご寄付を随時受け付けております。寄付金控除の対象となりますので、領収書等が必要な方は**事前に**ご連絡ください。

---

Happy slideshow-making! 🎬
