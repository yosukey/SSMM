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

**3. Enjoy Automatic, High-Speed Performance**

Forget about complicated video settings. The app automatically detects your computer’s hardware (like NVIDIA, Intel, AMD, or Apple silicon) and chooses the fastest way to create your video. It all happens in the background.

**4. Get Consistent, Professional Audio**

If your audio was recorded at different times or volumes, this feature automatically balances the sound levels throughout your entire video. No more sections that are too loud or too quiet.

**5. Protect and Brand Your Content with Watermarks**

Easily add a text watermark—like your name or website—to your video. You can fully customize its color, transparency, and rotation to protect and brand your work. This feature is also perfect for managing your workflow. You can temporarily add a watermark like "DRAFT" or "For Review Only" to indicate a video isn't final, which is great for sharing progress with colleagues or clients.

**6. Make Your Videos Easy to Navigate with Chapters**

The app can add chapters for your presentation, allowing viewers to jump to the sections they care about. It also creates a timestamped chapter list that you can copy and paste directly into your YouTube video description, saving you time.

---

## 📦 Setup

Ready-to-run applications for **Windows and macOS** are available in the repository. This is the easiest way to get started.

#### Limitations of the Ready-to-Run Application
Please be aware of the following limitations regarding these applications:

* **Security Warning on First Launch**: The application is not digitally signed, so your operating system will likely show a security warning. You will need to manually approve it to run.
* **Slower Startup & Larger File Size**: These applications are built using PyInstaller. This bundles Python and all dependencies into a single file, which makes the file size larger and the initial startup a bit slower as the app unpacks itself.

### Running with Python 🐍

1. **Install Python 3.10+** (3.10 or newer). See Python 3.12+ note below.
2. **Clone this repository**, then install deps:
   ```terminal
   pip install -r requirements.txt
   ```
3. **Install FFmpeg** (if you don’t already have it):
   - **Windows**: `winget install Gyan.FFmpeg` (or use the app’s “Install FFmpeg” menu).
   - **macOS**: `brew install ffmpeg`
   - **Linux**: Use your distro’s package manager (e.g., `sudo apt install ffmpeg`) or a static build.
   
   The app will auto-detect FFmpeg in this order:
   [1] `~/ffmpeg-bin` → [2] system `PATH` → [3] common Homebrew paths (macOS: `/opt/homebrew/bin`, `/usr/local/bin`) → [4] the FFmpeg bundled with the packaged app (release builds only)

> Tip: If you want a portable setup, place `ffmpeg` and `ffprobe` in `~/ffmpeg-bin/`.

### Python 3.12+ users
- The theme package **pyqtdarktheme** currently **declares support up to Python 3.11**.  
- The app itself **works on Python 3.12+**, but `pip` may refuse to install pyqtdarktheme due to the package metadata.
- Workaround (tested): install pyqtdarktheme manually with `--ignore-requires-python`:
  ```terminal
  pip install pyqtdarktheme==2.1.0 --ignore-requires-python
  ```
  Then install the remaining requirements as usual:
  ```terminal
  pip install -r requirements.txt
  ```

---

## 🚀 Quick Start

1. **Create a project folder** and put **exactly one PDF** in it (your slide deck).
2. Drop in **audio** and/or **video** assets you’ll use (any FFmpeg-supported common formats) into the project folder.
3. Run the app:

   Click the app icon or,
   ```terminal
   python main.py
   ```
4. In the UI, follow the top-to-bottom steps:
   - **Step 1**: Choose your project folder (with the PDF and the other materials).
   - **Step 2**: Choose an output folder.
      > **Important:** The application will create a temporary subfolder within your chosen location to store working files during the video creation process. For this reason, please select a folder on a fast local disk with sufficient free space. Avoid using network drives or USB flash storage, as these connections can be less stable and may cause unexpected errors or failures during processing.
   - **Step 3-1**: In **Slide Settings**, assign audios or videos to slides.
   - **Step 3-2**: Adjust parameters.
   - **Step 4**: Click **Check Files**. After the validator runs, go to the Validation Result tab to review the summary of your source files and see details if any errors were found.
      - **Preview**: This is an optional step. Export a **single-page preview** to test your current settings before the full render.
   - **Step 5**: **Create Video**.

You can also **Save/Load** `settings.toml` to reproduce a project settings later.

You can additionally **import a [DougaMeijin](https://github.com/yosukey/DougaMeijin) project file** (`.dmj`) via **File → Import DougaMeijin Project...**. You choose the folder to extract into, the archive is unpacked into a new subfolder there, its page images are combined into a PDF, and the per-page narration is brought in as audio materials so you can continue editing in SSMM. (This is a one-way import; SSMM does not write the `.dmj` format.)

---

## 🧠 How It Works (High-level)

1. **Encoder discovery & tests** — On first use, the app lists available FFmpeg encoders and actually *tests* a short render to verify they work on your machine.
2. **PDF analysis & caching** — The validator reads page count and dimensions and renders page thumbnails (stored as Base64). It also computes a perceptual hash (p-hash) per page to detect content changes quickly.
3. **Media probing** — All media files in the project folder are probed once and cached (duration, audio streams, video dimensions/rotation/sample aspect ratio/DAR, etc.).
4. **Slide table** — The UI presents one row per PDF page where you assign a material (Silent / Audio / Video), pick the audio stream, set chapter titles, and configure PinP (position/scale/effects). For videos, a live PinP overlay is drawn on the thumbnail preview to show the effective region.
5. **Export** — For each slide:
   - **Silent**: Render the page as a still with a silent segment of the given slide duration.
   - **Audio**: Combine the page image with the selected audio stream to make a segment.
   - **Video**: Overlay the chosen video as PinP on top of the page image, with optional effects (e.g., circle mask or chroma key) at your chosen position/scale.
   - Segments are then concatenated; optional **EBU R128 loudness normalization** and **watermark** are applied; final movie is written using your selected codec/encoder and pass/mode.

---

## ⚙️ Parameters (selected)

- **Resolution** (e.g., `1920x1080`) and **FPS** (e.g., 30)
- **Codec / Encoder** (software `libx264`/`libx265`/`libaom-av1`/`mpeg4`, or available HW encoders)
- **Mode**: Quality (CQP/CRF) / VBR / CBR
- **Audio**: bitrate (e.g., `160k`), sample rate (e.g., `32000`), channels (1/2)
- **Loudness normalization**: off / 1-pass / 2-pass
- **Watermark**: text, opacity, color, font family/size, rotation, tiling
- **Chapters**: embed chapters into the MP4 file and also export a companion file formatted for YouTube descriptions

> Availability of HW encoders depends on your machine and your FFmpeg build.

## 🔐 Internal Encoding Parameters

To ensure final video quality and compatibility, this application internally sets several encoding parameters that are not exposed in the user interface. The main fixed parameters are as follows:

### Video Settings

```-pix_fmt yuv420p```

Purpose: This is a standard pixel format that ensures maximum playback compatibility across a wide range of devices and software.

```-colorspace bt709 (and related parameters)```

Purpose: Specifies the standard color space for HD video, ensuring that colors are displayed correctly and consistently on most displays.

```-g <FPS * 2> (GOP Size)```

Purpose: Sets a keyframe interval of approximately every two seconds. This is a common setting that provides a good balance between seeking performance and compression efficiency.

### Profile & Preset

Purpose: For each video codec (e.g., H.264, H.265), a standard profile (high, main, etc.) and preset (medium, p5, etc.) are automatically selected to provide a good balance between quality and encoding speed.

### Audio Settings

```-c:a aac```

Purpose: The audio codec is fixed to AAC (Advanced Audio Coding), which is the most widely used and compatible audio format for the MP4 container.

### Container Settings

```-movflags +faststart```

Purpose: This option places the file's metadata at the beginning of the file, which allows for streaming playback (i.e., the video can start playing while it is still being downloaded).

---

## 🖥️ Launch Options

```terminal
python main.py --verbose
```

```terminal
python main.py /path/to/your/project
```

```terminal
python main.py /path/to/settings.toml
```

- `-v/--verbose` mirrors stdout/stderr to the app’s Debug panel.
- If you pass a **folder**: the app will scan it for the single PDF and media files.
- If you pass a **.toml**: the app will restore everything from that file.

---

## ❓ FAQ

**Q. The app can’t find FFmpeg.**  
A. Put `ffmpeg`/`ffprobe` into `~/ffmpeg-bin/`, or install via `winget`/`brew`, or make sure they’re on your PATH.

**Q. Hardware encoders don’t show up.**  
A. They depend on both your hardware and how FFmpeg was built. Try a different build or fall back to software encoders.

**Q. What formats can I use?**  
A. The application supports the following common file types for your materials:

- Video: MP4, MOV, AVI
- Audio: MP3, WAV, FLAC

While these file extensions are supported, they can contain audio and video encoded in many different ways. Due to this wide variety, it cannot be guaranteed that every file will work perfectly, even if the extension matches the supported list.

---

## 🙏 Acknowledgments

- FFmpeg and the broader open-source multimedia community
- PySide6 / Qt for Python
- pypdfium2 (PDFium), Pillow (PIL), ImageHash, PyQtDarkTheme

### Third-Party Components & Licenses

SSMM is distributed as a self-contained application that bundles or links the
components below. All of them are permissive licenses, or copyleft licenses
(LGPL) whose obligations are satisfied by dynamic linking together with the
notices and source references provided in the application's "Licenses" tab.
This keeps the distributed application consistent with SSMM's own MIT license.

| Component | Role | License |
| --- | --- | --- |
| PySide6 / Qt for Python | GUI toolkit | LGPL-3.0 |
| pypdfium2 (bundles Google PDFium) | PDF rendering & reading | Apache-2.0 / BSD-3-Clause (PDFium: BSD-3-Clause) |
| Pillow | Image processing & image→PDF | HPND (MIT-style) |
| PyQtDarkTheme | Qt theme | MIT |
| psutil | System info | BSD-3-Clause |
| ImageHash | Perceptual hashing | BSD-2-Clause |
| NumPy | Numerical computing | BSD-3-Clause |
| toml | TOML parsing | MIT |
| packaging | Version handling | Apache-2.0 / BSD-2-Clause |
| FFmpeg (bundled in release builds) | Audio/video encoding | LGPL-2.1 |
| Noto Sans fonts (bundled) | Text rendering | SIL Open Font License 1.1 |

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
