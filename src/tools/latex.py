import subprocess
from pathlib import Path
import shutil
import re
import unicodedata


SUPPORTED_COMPILERS = (
    "pdflatex",
    "xelatex",
    "lualatex",
)


def normalize_latex_unicode(source: str) -> str:
    """Replace Unicode spacing characters that pdflatex cannot reliably read."""
    return "".join(" " if unicodedata.category(character) == "Zs" else character for character in source)


def latex_failure_message(output: str) -> str:
    """Extract the useful error and source line from a verbose compiler transcript."""
    lines = output.replace("\r\n", "\n").splitlines()
    error_index = next((index for index, line in enumerate(lines) if line.lstrip().startswith("!")), None)
    if error_index is None:
        return "LaTeX compilation failed. Check the CV source for invalid LaTeX or unsupported characters."

    reason = [lines[error_index].lstrip().removeprefix("!").strip()]
    for line in lines[error_index + 1:error_index + 5]:
        stripped = line.strip()
        if not stripped or stripped.startswith(("See the LaTeX", "Type  H", "...", "l.")):
            break
        reason.append(stripped)

    context = next((line.strip() for line in lines[error_index + 1:] if re.match(r"l\.\d+", line.strip())), "")
    message = "LaTeX compilation failed: " + " ".join(reason)
    if context:
        message += f" Near {context}"
    return message[:900]


def find_latex_compiler() -> str:
    for name in SUPPORTED_COMPILERS:
        executable = shutil.which(name)
        if executable:
            return executable

    common_locations = (
        Path.home()
        / "AppData"
        / "Local"
        / "Programs"
        / "MiKTeX"
        / "miktex"
        / "bin"
        / "x64"
        / "pdflatex.exe",
        Path("C:/Program Files/MiKTeX/miktex/bin/x64/pdflatex.exe"),
    )

    for executable in common_locations:
        if executable.exists():
            return str(executable)

    raise RuntimeError(
        "No LaTeX compiler is installed or available on PATH. "
        "Install MiKTeX or TeX Live, restart the terminal, and try again."
    )


def compile_latex_to_pdf(
    tex_path: str,
    output_dir: str | None = None,
) -> str:

    tex_file = Path(tex_path)

    if not tex_file.exists():
        raise FileNotFoundError(
            f"LaTeX file not found: {tex_path}"
        )

    if output_dir is None:
        output_dir = str(tex_file.parent)

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    source = tex_file.read_text(encoding="utf-8")
    normalized_source = normalize_latex_unicode(source)
    if normalized_source != source:
        tex_file.write_text(normalized_source, encoding="utf-8")
    compiler = find_latex_compiler()
    compiler_options = []
    if "miktex" in compiler.lower():
        compiler_options.append("--enable-installer")

    try:
        subprocess.run(
            [
                compiler,
                *compiler_options,
                "-interaction=nonstopmode",
                "-halt-on-error",
                f"-output-directory={output_path}",
                str(tex_file),
            ],
            check=True,
            capture_output=True,
            text=True,
            errors="replace",
        )
    except subprocess.CalledProcessError as error:
        compiler_output = (error.stdout or error.stderr or "").strip()
        raise RuntimeError(latex_failure_message(compiler_output)) from error

    pdf_path = (
        output_path
        / f"{tex_file.stem}.pdf"
    )

    if not pdf_path.exists():
        raise RuntimeError(
            "PDF compilation failed."
        )

    return str(pdf_path)
