import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from ssmm import config
from ssmm.models import ProjectParameters, Slide
from ssmm.settings_manager import SettingsManager
from ssmm.validator import ProjectValidator
from ssmm.video_processing import OutputCommitError, VideoProcessor

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _make_pdf(path: Path, pages: int = 2):
    images = [Image.new("RGB", (320, 180), (40 * i, 80, 120)) for i in range(pages)]
    images[0].save(path, "PDF", save_all=True, append_images=images[1:])


# --- settings loading -------------------------------------------------------

def test_legacy_labels_and_types_are_coerced(qapp):
    manager = SettingsManager(main_window=None)
    params = ProjectParameters()
    params.encoding_mode = "Bitrate (VBR)"          # label stored by older versions
    params.encoding_pass = "2-Pass"
    params.normalize_loudness_mode = "1-Pass (Faster)"
    params.encoding_value = "4000"
    params.add_watermark = "true"
    params.watermark_opacity = 250
    params.hardware_encoding = "UnknownGPU"
    manager._coerce_and_validate_parameters(params)
    assert params.encoding_mode == "VBR"
    assert params.encoding_pass == "TWO_PASS"
    assert params.normalize_loudness_mode == "ONE_PASS"
    assert params.encoding_value == 4000
    assert params.add_watermark is True
    assert params.watermark_opacity == 100
    assert params.hardware_encoding is None


def test_mpeg4_quality_value_uses_qscale_range(qapp):
    manager = SettingsManager(main_window=None)
    params = ProjectParameters(codec='MPEG-4 Part 2', encoding_mode="QUALITY", encoding_value=45)
    manager._coerce_and_validate_parameters(params)
    assert params.encoding_value == 31


# --- validator ------------------------------------------------------------

def test_pdf_structure_ignores_stale_cancel_flag(tmp_path):
    pdf = tmp_path / "deck.pdf"
    _make_pdf(pdf, pages=3)
    validator = ProjectValidator()
    validator.cancel()  # left over from a cancelled validation run
    assert validator.get_pdf_structure(pdf)['page_count'] == 3
    validator.start_validation()
    assert not validator.is_canceled()


def test_file_hash_full_and_sampled(tmp_path, monkeypatch):
    validator = ProjectValidator()
    small = tmp_path / "a.mp3"
    small.write_bytes(b"x" * 1000)
    assert len(validator.get_file_hash(small)) == 64

    monkeypatch.setattr(config, "FULL_HASH_MAX_BYTES", 100)
    monkeypatch.setattr(config, "SAMPLED_HASH_BLOCK_BYTES", 10)
    big = tmp_path / "b.mp4"
    big.write_bytes(bytes(range(256)) * 4)
    first = validator.get_file_hash(big)
    data = bytearray(big.read_bytes())
    data[-1] ^= 0xFF  # change inside the sampled tail block
    big.write_bytes(bytes(data))
    validator.file_hash_cache.clear()
    assert validator.get_file_hash(big) != first


def test_snapshot_reports_unreadable_files_consistently(tmp_path, monkeypatch):
    _make_pdf(tmp_path / "deck.pdf")
    (tmp_path / "ok.mp3").write_bytes(b"audio")
    (tmp_path / "bad.mp3").write_bytes(b"audio")
    (tmp_path / "notes.txt").write_text("ignored")
    validator = ProjectValidator()
    real = validator.get_file_hash
    monkeypatch.setattr(validator, "get_file_hash", lambda p: "" if p.name == "bad.mp3" else real(p))
    snapshot, unreadable = validator.snapshot_project_files(tmp_path)
    assert set(snapshot) == {"deck.pdf", "ok.mp3"}
    assert unreadable == ["bad.mp3"]
    # A second snapshot (as taken before rendering) compares equal.
    assert validator.snapshot_project_files(tmp_path)[0] == snapshot


# --- renderer ---------------------------------------------------------------

def test_encoding_options():
    vp = VideoProcessor()
    params = ProjectParameters(codec="H.264/MPEG-4 AVC", encoding_mode="VBR", encoding_value=3000, encoding_pass="TWO_PASS")
    opts = vp._get_video_encoding_options(params, pass_num=2)
    assert '-level' not in opts
    assert opts[opts.index('-pass') + 1] == '2'
    assert opts[opts.index('-maxrate') + 1] == '4500k'

    nvenc = ProjectParameters(codec="H.264/MPEG-4 AVC", hardware_encoding="NVIDIA", encoding_mode="CBR",
                              encoding_value=3000, encoding_pass="TWO_PASS")
    opts = vp._get_video_encoding_options(nvenc)
    assert '-pass' not in opts and opts[opts.index('-multipass') + 1] == 'fullres'
    assert not vp._uses_two_pass(nvenc, 'h264_nvenc')

    qsv = ProjectParameters(codec="H.264/MPEG-4 AVC", hardware_encoding="Intel", encoding_mode="VBR",
                            encoding_pass="TWO_PASS")
    assert '-pass' not in vp._get_video_encoding_options(qsv)


def test_commit_output_keeps_render_when_destination_is_locked(tmp_path):
    rendered = tmp_path / "out.tmp.mp4"
    rendered.write_bytes(b"video")
    blocked = tmp_path / "out.mp4"
    blocked.mkdir()
    (blocked / "keep").write_text("x")  # a non-empty directory cannot be replaced
    with pytest.raises(OutputCommitError) as excinfo:
        VideoProcessor._commit_output(rendered, blocked)
    assert rendered.exists()
    assert str(rendered) in str(excinfo.value)


def test_ffmetadata_escape():
    assert VideoProcessor._escape_ffmetadata("a=b;c#d\\e\nf") == "a\\=b\\;c\\#d\\\\e\\\nf"


def test_chapter_metadata_matches_timeline(tmp_path):
    vp = VideoProcessor()
    model = type("M", (), {})()
    model.parameters = ProjectParameters(fps=30)
    model.slides = [Slide(duration=2.0, interval_to_next=1, chapter_title="A"),
                    Slide(duration=1.01, interval_to_next=1),
                    Slide(duration=3.0, chapter_title="B")]
    path = tmp_path / "meta.txt"
    vp._generate_ffmetadata(model, path)
    text = path.read_text()
    assert "START=0\nEND=5033\ntitle=A" in text
    assert "START=5033\nEND=8033\ntitle=B" in text


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
@pytest.mark.parametrize("matrix_rotation", [-90, 90, 180])
def test_rotation_filters_match_ffmpeg_autorotate(tmp_path, matrix_rotation):
    from ssmm.media_utils import normalize_rotation, rotation_filters

    def run(*args):
        subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)

    src = tmp_path / "src.mp4"
    rotated = tmp_path / "rot.mp4"
    run("-f", "lavfi", "-i", "testsrc=s=96x64:r=5:d=1", "-c:v", "libx264", "-pix_fmt", "yuv444p", str(src))
    run("-display_rotation", str(matrix_rotation), "-i", str(src), "-c", "copy", str(rotated))
    run("-i", str(rotated), "-frames:v", "1", "-vf", "format=rgb24", str(tmp_path / "auto.png"))
    filters = ",".join(rotation_filters(normalize_rotation(matrix_rotation)) + ["format=rgb24"])
    run("-noautorotate", "-i", str(rotated), "-frames:v", "1", "-vf", filters, str(tmp_path / "app.png"))
    assert Image.open(tmp_path / "auto.png").tobytes() == Image.open(tmp_path / "app.png").tobytes()


# --- UI helpers -------------------------------------------------------------

def test_emitting_stream_emits_whole_lines(qapp):
    from ssmm.main_window import EmittingStream
    stream = EmittingStream()
    lines = []
    stream.text_written.connect(lambda text, source: lines.append(text))
    print("hello", file=stream)
    stream.write("partial")
    assert lines == ["hello"]
    stream.flush()
    assert lines == ["hello", "partial"]


@pytest.mark.parametrize("version, expected", [
    ("1.2.0", "1.2.0"),
    ("1.2.0-3-gabc1234", "1.2.0"),
    ("abc1234", None),
])
def test_release_version_parsing(version, expected):
    from ssmm.main_window import MainWindow
    parsed = MainWindow._release_version(version)
    assert (str(parsed) if parsed else None) == expected


def test_letterbox_matches_output_aspect():
    from ssmm.ui_helpers import letterbox_to_output
    page = Image.new("RGB", (400, 300))  # 4:3 page
    framed = letterbox_to_output(page, 1920, 1080)
    assert framed.size == (533, 300)
    assert letterbox_to_output(Image.new("RGB", (320, 180)), 1920, 1080).size == (320, 180)


def test_settings_round_trip_and_legacy_file(tmp_path, qapp, monkeypatch):
    from types import SimpleNamespace
    from PySide6.QtWidgets import QMessageBox
    from ssmm.models import ProjectModel
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.Ok))

    _make_pdf(tmp_path / "deck.pdf", pages=2)
    (tmp_path / "[001]a.mp3").write_bytes(b"audio")
    fake_window = SimpleNamespace(validator=ProjectValidator(), __version__="test")
    manager = SettingsManager(main_window=fake_window)

    model = ProjectModel(project_folder=tmp_path)
    model.parameters = ProjectParameters(encoding_mode="CBR", encoding_value=5000, encoding_pass="TWO_PASS")
    model.slides = [Slide(filename="[001]a.mp3", chapter_title="Intro", interval_to_next=2, transition_to_next="Fade"),
                    Slide(filename=config.SILENT_MATERIAL_NAME, duration=4)]
    assert manager._perform_save(tmp_path / "settings.toml", model)

    loaded = manager.load_from_file(tmp_path / "settings.toml")
    assert loaded.parameters.encoding_mode == "CBR"
    assert loaded.parameters.encoding_pass == "TWO_PASS"
    assert loaded.slides[0].filename == "[001]a.mp3"
    assert loaded.slides[0].transition_to_next == "Fade"
    assert loaded.slides[1].duration == 4

    legacy = (tmp_path / "settings.toml").read_text(encoding="utf-8")
    legacy = legacy.replace('encoding_mode = "CBR"', 'encoding_mode = "Bitrate (CBR)"')
    legacy = legacy.replace('interval_to_next = 2', 'interval_to_next = 9999')
    (tmp_path / "settings.toml").write_text(legacy, encoding="utf-8")
    loaded = manager.load_from_file(tmp_path / "settings.toml")
    assert loaded.parameters.encoding_mode == "CBR"
    assert loaded.slides[0].interval_to_next == config.INTERVAL_RANGE[1]


@pytest.mark.parametrize("codec, ext", [
    ("libx264", ".mov"), ("hevc_nvenc", ".mov"), ("mpeg4", ".mov"),
    ("libaom-av1", ".mp4"), ("av1_nvenc", ".mp4"), ("av1_qsv", ".mp4"), ("av1_amf", ".mp4"),
])
def test_intermediate_container_per_encoder(codec, ext):
    assert VideoProcessor._intermediate_container_for(codec) == ext


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
@pytest.mark.parametrize("encoder", ["libx264", "libaom-av1"])
def test_intermediate_segment_accepts_encoder_with_pcm(tmp_path, encoder):
    # FFmpeg's MOV muxer rejects AV1; the chosen container must accept every encoder.
    encoders = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    if f" {encoder} " not in encoders:
        pytest.skip(f"{encoder} not available")
    out = tmp_path / f"seg{VideoProcessor._intermediate_container_for(encoder)}"
    extra = ["-cpu-used", "8", "-crf", "50", "-b:v", "0"] if encoder == "libaom-av1" else []
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=64x48:r=10:d=0.5",
                    "-f", "lavfi", "-i", "sine=d=0.5", "-t", "0.5", "-c:v", encoder, *extra, "-pix_fmt", "yuv420p",
                    "-c:a", config.INTERMEDIATE_AUDIO_CODEC, str(out)], check=True)
    assert out.stat().st_size > 0


def test_libx265_two_pass_uses_x265_stats():
    vp = VideoProcessor()
    params = ProjectParameters(codec="H.265/HEVC", encoding_mode="VBR", encoding_value=3000, encoding_pass="TWO_PASS")
    opts = vp._get_video_encoding_options(params, pass_num=2, pass_log_prefix="C:\\tmp\\o'k\\slide_001")
    assert '-pass' not in opts and '-passlogfile' not in opts
    assert opts[opts.index('-x265-params') + 1] == "pass=2:stats='C:/tmp/o'\\''k/slide_001-x265.log'"
    x264 = ProjectParameters(codec="H.264/MPEG-4 AVC", encoding_mode="VBR", encoding_value=3000, encoding_pass="TWO_PASS")
    opts = vp._get_video_encoding_options(x264, pass_num=1, pass_log_prefix="/tmp/p")
    assert opts[opts.index('-passlogfile') + 1] == "/tmp/p"


def test_av1_intermediate_support_check(monkeypatch, qapp):
    import ssmm.validator as validator_module
    from ssmm.models import ValidationMessages
    av1 = ProjectParameters(codec="AV1")
    if HAS_FFMPEG:
        messages = ValidationMessages()
        ProjectValidator()._check_intermediate_container_support(av1, messages)
        assert not messages.has_errors()
    # An FFmpeg that cannot write the container is reported as an error.
    monkeypatch.setattr(validator_module, "get_ffmpeg_path", lambda: Path(shutil.which("false") or "/bin/false"))
    messages = ValidationMessages()
    ProjectValidator()._check_intermediate_container_support(av1, messages)
    assert messages.has_errors()
    messages = ValidationMessages()
    ProjectValidator()._check_intermediate_container_support(ProjectParameters(codec="H.264/MPEG-4 AVC"), messages)
    assert not messages.has_errors()


def test_concat_list_uses_absolute_paths(tmp_path, monkeypatch):
    # The concat demuxer resolves relative entries against the list file's folder,
    # which doubled the path for a relative output folder on Python < 3.12.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "out").mkdir()
    vp = VideoProcessor()
    list_path = vp._write_concat_list(Path("out") / "list.txt", [Path("out") / "a.mov", Path("out") / "it's.mov"])
    lines = list_path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == f"file '{(tmp_path / 'out' / 'a.mov').as_posix()}'"
    assert lines[1].endswith("/out/it'\\''s.mov'")
