import hashlib
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Md5Pair:
    bootstrap_md5: str
    dat_file_md5: str


_MAPPING = {
    "1.7.10": Md5Pair("A895FE657915D58F55919CEACD30209D", "538D33D5F35EF01736EDA30F94C61DF6"),
    "1.8.9": Md5Pair("A895FE657915D58F55919CEACD30209D", "0CF2074AA7D4B543E35A3D6BB57AF861"),
    "1.16": Md5Pair("7B101583C3965371B89A3C9115B27526", "B0712F34B0A584D05D9D29FA68759E29"),
    "1.12.2": Md5Pair("A895FE657915D58F55919CEACD30209D", "51581ADD89B8AC5A0D8CCDD0E33EE1DE"),
    "1.18": Md5Pair("C3BD2115F23F6FE4B2ADCC7FC4DEFFEA", "56677A2BB31E18246FA241FB02E16D0E"),
    "1.20": Md5Pair("2A7A476411A1687A56DC6848829C1AE4", "D285CBF97D9BA30D3C445DBF1C342634"),
    "1.21": Md5Pair("684528BF492A84489F825F5599B3E1C6", "574033E7E4841D8AC4C14D7FA5E05337"),
}


def get_md5_pair(version: str) -> Md5Pair:
    if version not in _MAPPING:
        raise KeyError(f"unsupported game version: {version}")
    pair = _MAPPING[version]
    dat_file_md5 = _load_local_dat_file_md5(version) or pair.dat_file_md5
    return Md5Pair(pair.bootstrap_md5, dat_file_md5)


def _load_local_dat_file_md5(version: str) -> str:
    dat_path = _find_local_dat_file(version)
    if dat_path is None:
        return ""
    digest = hashlib.md5(dat_path.read_bytes()).hexdigest().upper()
    return digest if len(digest) > 31 else ""


def _find_local_dat_file(version: str) -> Path | None:
    for mc_root in _candidate_minecraft_roots():
        dat_path = mc_root / "versions" / version / f"{version}.dat"
        if dat_path.is_file():
            return dat_path
    return None


def _candidate_minecraft_roots() -> list[Path]:
    roots: list[Path] = []
    seen: set[str] = set()

    def add_root(path: Path) -> None:
        normalized = str(path).lower()
        if normalized in seen:
            return
        seen.add(normalized)
        roots.append(path)

    def add_base(path_value: str) -> None:
        value = (path_value or "").strip()
        if not value:
            return
        base = Path(value).expanduser()
        if base.name.lower() == ".minecraft":
            add_root(base)
            return
        add_root(base / ".minecraft")

    for item in os.getenv("NEL_MC_BASE_PATH", "").split(os.pathsep):
        add_base(item)
    add_base(str(Path.cwd() / ".game_cache" / "Game" / "Base"))
    add_base(str(Path.home() / ".camellia" / ".game_cache" / "Game" / "Base"))
    add_base(os.getenv("APPDATA", ""))
    add_base(str(Path.home()))
    return roots
