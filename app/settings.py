"""Local settings are independent of the current working directory."""
import hashlib
import json
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


def configure():
    load_dotenv(ROOT/".env", override=False)
    # User-supplied pins take precedence over the installation previously verified here.
    pin_file = ROOT/"instance"/"pat-install.json"
    if pin_file.is_file():
        pin = json.loads(pin_file.read_text(encoding="utf-8"))
        for key in ("AG_PAT_EXE","AG_PAT_VERSION","AG_PAT_SHA256"):
            if key in pin:
                os.environ.setdefault(key,pin[key])
    for key in ("AG_PAT_EXE","AG_PAT_RUNTIME","AG_DB"):
        if os.environ.get(key):
            os.environ[key]=str((ROOT/Path(os.environ[key]).expanduser()).resolve())


def installation(exe, version):
    from .pat import Pat
    path=Path(exe).expanduser().resolve()
    if not path.is_file():
        raise SystemExit(f"PAT console not found at {path}")
    pins={"AG_PAT_EXE":str(path),"AG_PAT_VERSION":version,
          "AG_PAT_SHA256":hashlib.sha256(path.read_bytes()).hexdigest()}
    trial=Pat(dict(os.environ,**pins))
    if trial.ready:
        dest=ROOT/"instance"/"pat-install.json"
        dest.parent.mkdir(exist_ok=True)
        # A second launcher must always see a complete settings file.
        import tempfile
        with tempfile.NamedTemporaryFile("w",encoding="utf-8",dir=dest.parent,delete=False) as f:
            json.dump(pins,f,indent=2)
        os.replace(f.name,dest)
    return trial


def remember_pat(exe, version):
    trial=installation(exe,version)
    if not trial.ready:
        raise SystemExit(f"PAT calibration failed: {trial.detail}. No settings saved.")
    print("Saved the calibrated PAT installation. Run integrations/check_pat.py for the acceptance suite.")


def configured_solver():
    from .pat import Pat
    path=ROOT/"pat_lib"/"PAT3.Console.exe"
    if os.name=="nt" and not any(os.environ.get(k) for k in ("AG_PAT_EXE","AG_PAT_VERSION","AG_PAT_SHA256")) and path.is_file():
        try:
            import win32api
            props=win32api.GetFileVersionInfo(str(path),"\\")
            a,b=props["FileVersionMS"],props["FileVersionLS"]
            version=f"{a >> 16}.{a & 65535}.{b >> 16}.{b & 65535}"
        except (ImportError,OSError,KeyError):
            solver=Pat({})
            solver.detail="Could not read the bundled PAT file version. Use run.py --configure-pat with your verified version."
            return solver
        return installation(path,version)
    return Pat()
