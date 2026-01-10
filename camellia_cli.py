import asyncio
import logging

from camellia.mc.md5_mapping import get_md5_pair
from camellia.plugins import get_plugin_manager
from camellia.mc.proxy import MinecraftProxy, ProxyConfig
from camellia.api.n4399 import LoginError, login_with_password
from camellia.api.wpf_launcher import ApiError, WPFLauncherClient
from camellia.mc.yggdrasil import GameProfile, ModList, StandardYggdrasil, UserProfile, YggdrasilData


def _read_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _extract_cookie(raw_text: str) -> str:
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    for line in lines:
        if line.startswith("{") and "sauth_json" in line:
            return line
    for idx, line in enumerate(lines):
        if line.lower().startswith("cookies"):
            for next_line in lines[idx + 1 :]:
                if next_line.startswith("{") and "sauth_json" in next_line:
                    return next_line
    start = raw_text.find("{")
    end = raw_text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return raw_text[start : end + 1].strip()
    raise ValueError("could not locate sauth_json in cookie file")


def _prompt(text: str) -> str:
    print(text)
    return input("> ").strip()


def _select_server(client: WPFLauncherClient) -> str:
    servers = []
    offset = 0
    page_size = 10
    while True:
        if len(servers) == offset:
            servers.extend(client.get_available_servers(offset, page_size))
        print("\nAvailable servers:")
        for idx, server in enumerate(servers, start=1):
            print(f"{idx}. {server.name} (id={server.entity_id})")
        choice = _prompt("Select server number, or 'n' for next page, or 's' to search in loaded list")
        if choice.lower() == "n":
            offset += page_size
            continue
        if choice.lower() == "s":
            keyword = _prompt("Search keyword")
            matches = [s for s in servers if keyword.lower() in s.name.lower() or keyword.lower() in s.brief_summary.lower()]
            if not matches:
                print("No matches in loaded list. Use 'n' to load more.")
                continue
            print("Matches:")
            for item in matches:
                idx = servers.index(item) + 1
                print(f"{idx}. {item.name} (id={item.entity_id})")
            continue
        try:
            index = int(choice)
        except ValueError:
            print("Invalid selection.")
            continue
        if index < 1 or index > len(servers):
            print("Out of range.")
            continue
        return servers[index - 1].entity_id


def _select_character(client: WPFLauncherClient, game_id: str) -> str:
    characters = client.get_characters(game_id)
    if characters:
        print("\nCharacters:")
        for idx, character in enumerate(characters, start=1):
            print(f"{idx}. {character.name}")
        choice = _prompt("Select character number or type 'new' to create")
        if choice.lower() != "new":
            try:
                index = int(choice)
                if 1 <= index <= len(characters):
                    return characters[index - 1].name
            except ValueError:
                pass
        print("Creating new character.")
    name = _prompt("Character name")
    if not name:
        raise ValueError("character name required")
    client.create_character(game_id, name)
    return name


def _try_yggdrasil(client: WPFLauncherClient, game_id: str, version_name: str, user_id: str, user_token: str) -> None:
    server_id = _prompt("Optional: enter serverId for Yggdrasil join (empty to skip)")
    if not server_id:
        return
    info = client.fetch_fantnel_info()
    if not info.crc_salt:
        print("CRC salt unavailable, cannot run Yggdrasil join.")
        return
    try:
        pair = get_md5_pair(version_name)
    except KeyError as exc:
        print(str(exc))
        return
    profile = GameProfile(
        game_id=game_id,
        game_version=version_name,
        bootstrap_md5=pair.bootstrap_md5,
        dat_file_md5=pair.dat_file_md5,
        mods=ModList([]),
        user=UserProfile(user_id=int(user_id), user_token=user_token),
    )
    ygg_data = YggdrasilData(
        launcher_version=client.game_version,
        channel="netease",
        crc_salt=info.crc_salt,
    )
    ygg = StandardYggdrasil.with_random_server(ygg_data)
    try:
        ok, err = ygg.join_server(profile, server_id)
    except Exception as exc:  # pylint: disable=broad-except
        print(f"Yggdrasil join failed: {exc}")
        return
    if ok:
        print("Yggdrasil join success.")
    else:
        print(f"Yggdrasil join failed: {err}")


def _build_yggdrasil_profile(
    client: WPFLauncherClient, game_id: str, version_name: str, user_id: str, user_token: str
) -> tuple[GameProfile | None, YggdrasilData | None]:
    info = client.fetch_fantnel_info()
    if not info.crc_salt:
        print("CRC salt unavailable; proxy will skip Yggdrasil join.")
        return None, None
    try:
        pair = get_md5_pair(version_name)
    except KeyError as exc:
        print(str(exc))
        return None, None
    try:
        mod_list = client.get_mod_list(game_id, version_name, include_assets=True)
    except ApiError as exc:
        print(f"Mod list fetch failed: {exc} (using empty list)")
        mod_list = ModList([])
    profile = GameProfile(
        game_id=game_id,
        game_version=version_name,
        bootstrap_md5=pair.bootstrap_md5,
        dat_file_md5=pair.dat_file_md5,
        mods=mod_list,
        user=UserProfile(user_id=int(user_id), user_token=user_token),
    )
    ygg_data = YggdrasilData(
        launcher_version=client.game_version,
        channel="netease",
        crc_salt=info.crc_salt,
    )
    return profile, ygg_data


def main() -> int:
    get_plugin_manager().load_plugins(extras={"mode": "cli"})
    client = WPFLauncherClient()
    print("Camellia CLI (no proxy mode)")
    print("1) Cookie login (file)")
    print("2) 4399 login (account)")
    mode = _prompt("Choose login mode")

    try:
        if mode == "1":
            path = _prompt("Cookie file path [test_sauth]")
            if not path:
                path = "test_sauth"
            raw = _read_file(path)
            cookie = _extract_cookie(raw)
            auth = client.login_with_cookie(cookie)
        elif mode == "2":
            username = _prompt("4399 username")
            password = _prompt("4399 password")
            sauth_json = login_with_password(username, password)
            auth = client.login_with_cookie(sauth_json)
        else:
            print("Unsupported login mode.")
            return 1
    except (ApiError, LoginError, ValueError) as exc:
        print(f"Login failed: {exc}")
        return 1

    print(f"Login success. Entity ID: {auth.entity_id} (channel={auth.login_channel})")

    game_id = _select_server(client)
    detail = client.get_server_detail(game_id)
    address = client.get_server_address(game_id)
    version_name = detail.mc_versions[0].name if detail.mc_versions else ""
    if version_name:
        print(f"Server version: {version_name}")

    character_name = _select_character(client, game_id)
    print(f"Selected character: {character_name}")

    client.game_start(game_id)
    print("GameStart OK.")

    host = address.host or detail.server_address
    port = address.port or detail.server_port
    if host and port:
        print(f"Remote server: {host}:{port}")
    else:
        print("Server address not available from API.")

    print("\nConnection mode:")
    print("1) No proxy (direct connect)")
    print("2) Local proxy (recommended for online servers)")
    mode = _prompt("Choose mode")

    if mode == "1":
        if host and port:
            print(f"Connect your client to: {host}:{port}")
        if version_name:
            _try_yggdrasil(client, game_id, version_name, auth.entity_id, auth.token)
        else:
            print("Server version unavailable; skipping Yggdrasil join.")
        print("No proxy mode: joining may fail on online servers.")
        return 0

    if mode != "2":
        print("Unknown mode.")
        return 1

    if not (host and port):
        print("Missing server address; cannot start proxy.")
        return 1

    local_host = _prompt("Local listen host [127.0.0.1]") or "127.0.0.1"
    local_port_raw = _prompt("Local listen port [6445]") or "6445"
    try:
        local_port = int(local_port_raw)
    except ValueError:
        print("Invalid port.")
        return 1

    profile = None
    ygg_data = None
    if version_name:
        profile, ygg_data = _build_yggdrasil_profile(
            client, game_id, version_name, auth.entity_id, auth.token
        )
    else:
        print("Server version unavailable; proxy will skip Yggdrasil join.")

    print(f"Starting proxy at {local_host}:{local_port} -> {host}:{port}")
    print("Connect your Minecraft client to the local address above.")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    proxy = MinecraftProxy(
        ProxyConfig(
            listen_host=local_host,
            listen_port=local_port,
            forward_host=host,
            forward_port=port,
            nickname=character_name,
            game_id=game_id,
            ygg_profile=profile,
            ygg_data=ygg_data,
        )
    )
    try:
        asyncio.run(proxy.serve())
    except KeyboardInterrupt:
        print("Proxy stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
