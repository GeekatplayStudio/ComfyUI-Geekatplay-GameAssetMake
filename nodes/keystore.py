# =============================================================
# Geekatplay GameAssetMake — Secure local API key storage
# (c) Geekatplay Studio / Vladimir Chopine
#
# Keys are stored in the OS credential vault (Windows Credential
# Manager / macOS Keychain / Linux Secret Service) via `keyring`,
# NEVER in workflow JSON — so shared workflows leak nothing.
# Falls back to a gitignored local file if no vault is available.
# Also reads keys saved by ComfyUI-Blender-Toolbox's Credential
# Manager so existing credentials keep working.
# =============================================================
import os
import json

KEYRING_SERVICE = "Geekatplay-GameAssetMake"
TOOLBOX_SERVICE = "ComfyUI-Blender-Toolbox"  # shared with the Blender Toolbox pack

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FALLBACK_PATH = os.path.join(_ROOT, "config", "api_keys.local.json")  # gitignored

# Canonical credential names per provider (aliases cover Toolbox conventions)
PROVIDER_NAMES = {
    "tripo": ["Tripo", "TRIPO_API_KEY"],
    "meshy": ["Meshy", "MESHY_API_KEY"],
    # HiTem3D issues an AK/SK pair (Client ID + Client Secret) — stored separately.
    "hitem3d_access": ["HiTem3D Access Key", "HITEM3D_ACCESS_KEY"],
    "hitem3d_secret": ["HiTem3D Secret Key", "HITEM3D_SECRET_KEY"],
    # Legacy single-field entry: "AccessKey:SecretKey" (still read as a fallback)
    "hitem3d": ["HiTem3D", "Hitem3D", "HITEM3D_API_KEY"],
    "ollama": ["Ollama Cloud", "Ollama"],
}

try:
    import keyring
    from keyring.errors import KeyringError
    _HAVE_KEYRING = True
except ImportError:
    keyring = None
    KeyringError = Exception
    _HAVE_KEYRING = False


def _read_fallback():
    try:
        with open(FALLBACK_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_fallback(data):
    os.makedirs(os.path.dirname(FALLBACK_PATH), exist_ok=True)
    with open(FALLBACK_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def save_key(provider, value):
    """Persist a key for a provider ('tripo' | 'meshy' | 'hitem3d' | 'ollama')."""
    value = str(value or "").strip()
    if not value:
        return False
    name = PROVIDER_NAMES.get(provider, [provider])[0]
    if _HAVE_KEYRING:
        try:
            keyring.set_password(KEYRING_SERVICE, name, value)
            print(f"[GameAssetMake Keystore] Saved '{name}' key to the OS credential vault.")
            return True
        except KeyringError as exc:
            print(f"[GameAssetMake Keystore] Vault unavailable ({exc}); using local file fallback.")
    data = _read_fallback()
    data[name] = value
    _write_fallback(data)
    print(f"[GameAssetMake Keystore] Saved '{name}' key to {FALLBACK_PATH} (gitignored).")
    return True


def get_key(provider):
    """Look up a provider key: our vault -> Blender-Toolbox vault -> local file -> env var."""
    names = PROVIDER_NAMES.get(provider, [provider])
    if _HAVE_KEYRING:
        for service in (KEYRING_SERVICE, TOOLBOX_SERVICE):
            for name in names:
                try:
                    secret = keyring.get_password(service, name)
                except KeyringError:
                    secret = None
                if secret:
                    return secret.strip()
    data = _read_fallback()
    for name in names:
        if data.get(name):
            return str(data[name]).strip()
    for name in names:
        env = os.getenv(name.upper().replace(" ", "_").replace("-", "_"), "") or os.getenv(name, "")
        if env:
            return env.strip()
    return ""


def resolve_key(provider, entered_value="", remember=False):
    """
    Standard node-side flow: a value typed into the node wins (and is
    persisted when remember=True); otherwise the stored key is used.
    """
    entered_value = str(entered_value or "").strip()
    if entered_value:
        if remember:
            save_key(provider, entered_value)
        return entered_value
    return get_key(provider)


def resolve_hitem3d_keys(access_key="", secret_key="", remember=False):
    """
    HiTem3D uses a two-part credential (Access Key = Client ID,
    Secret Key = Client Secret). Typed values win and are persisted when
    remember=True; otherwise the stored pair is used. Falls back to a
    legacy combined "AccessKey:SecretKey" entry if one exists.
    Returns (access_key, secret_key).
    """
    access = str(access_key or "").strip()
    secret = str(secret_key or "").strip()

    if access and secret:
        if remember:
            save_key("hitem3d_access", access)
            save_key("hitem3d_secret", secret)
        return access, secret

    stored_access = access or get_key("hitem3d_access")
    stored_secret = secret or get_key("hitem3d_secret")

    if not (stored_access and stored_secret):
        legacy = get_key("hitem3d")
        if legacy and ":" in legacy:
            legacy_access, legacy_secret = legacy.split(":", 1)
            stored_access = stored_access or legacy_access.strip()
            stored_secret = stored_secret or legacy_secret.strip()

    return stored_access, stored_secret
