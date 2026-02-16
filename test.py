"""
This script downloads the Minecraft client mappings for a user-selected stable version,
converts them into a custom JSON format, and saves the output to 'mappings.json'.
"""
import re
import json
import requests
import argparse


def get_all_release_versions():
    """
    Retrieves a list of all stable (release) Minecraft versions from the Mojang version manifest.

    Returns:
        list: A list of version strings, sorted from newest to oldest, or None if an error occurs.
    """
    try:
        print("Fetching Minecraft version manifest...")
        response = requests.get("https://piston-meta.mojang.com/mc/game/version_manifest_v2.json", timeout=10)
        response.raise_for_status()  # Raise an exception for bad HTTP responses
        manifest = response.json()

        # Filter for release versions and extract their IDs
        release_versions = [v['id'] for v in manifest.get('versions', []) if v.get('type') == 'release']

        if release_versions:
            print(f"Found {len(release_versions)} stable versions.")
            return release_versions
        else:
            print("Error: Could not find any stable versions in the manifest.")
            return None
    except requests.exceptions.RequestException as e:
        print(f"Error while fetching the version manifest: {e}")
        return None
    except json.JSONDecodeError:
        print("Error: Failed to decode JSON response from Mojang's server.")
        return None


def present_version_menu(versions):
    """
    Displays an interactive menu for the user to select a Minecraft version.

    Args:
        versions (list): A list of version strings to display.

    Returns:
        str: The version string selected by the user, or None if they quit.
    """
    displayed_count = 0
    page_size = 10

    while True:
        # Determine the slice of versions to display for the current page
        end_index = displayed_count + page_size
        current_page_versions = versions[displayed_count:end_index]

        print("\nPlease select a Minecraft version:")
        print("-" * 30)

        for i, version in enumerate(current_page_versions, start=1):
            print(f"  {displayed_count + i}. {version}")

        print("-" * 30)

        options = []
        # Option to show more versions if available
        if end_index < len(versions):
            options.append("[m] Show more...")

        options.append("[q] Quit")
        print(" / ".join(options))

        choice = input("Enter your choice (number, 'm', or 'q'): ").lower().strip()

        if choice == 'q':
            return None
        elif choice == 'm' and end_index < len(versions):
            displayed_count = end_index
            continue
        elif choice.isdigit():
            try:
                selection_index = int(choice) - 1
                if 0 <= selection_index < len(versions):
                    selected_version = versions[selection_index]
                    print(f"You selected: {selected_version}")
                    return selected_version
                else:
                    print("Invalid number. Please try again.")
            except ValueError:
                print("Invalid input. Please enter a number from the list, 'm', or 'q'.")
        else:
            print("Invalid input. Please try again.")


def download_client_mappings(version):
    """
    Downloads the client mappings file for a specific Minecraft version.

    Args:
        version (str): The Minecraft version to download the mappings for.

    Returns:
        str: The text content of the mappings file, or None if an error occurs.
    """
    try:
        print(f"Fetching information for version {version}...")
        # First, get the version manifest to find the URL for the specific version details
        response = requests.get("https://piston-meta.mojang.com/mc/game/version_manifest_v2.json", timeout=10)
        response.raise_for_status()
        manifest = response.json()

        version_info_url = next((v['url'] for v in manifest['versions'] if v['id'] == version), None)

        if not version_info_url:
            print(f"Error: Could not find the information URL for version {version}.")
            return None

        print(f"Downloading detailed information for version {version}...")
        version_info_response = requests.get(version_info_url, timeout=10)
        version_info_response.raise_for_status()
        version_info = version_info_response.json()

        mappings_url = version_info.get("downloads", {}).get("client_mappings", {}).get("url")

        if not mappings_url:
            print(f"Error: Could not find the mappings URL for version {version}.")
            return None

        print(f"Downloading client mappings from {mappings_url}...")
        mappings_response = requests.get(mappings_url, timeout=30)
        mappings_response.raise_for_status()
        print("Mappings download complete.")
        return mappings_response.text
    except requests.exceptions.RequestException as e:
        print(f"Error while downloading mappings: {e}")
        return None
    except json.JSONDecodeError:
        print("Error: Failed to decode JSON response while fetching version details.")
        return None


def convert_java_type_to_jvm(java_type, class_map):
    """Converts a Java type to its internal JVM representation."""
    array_depth = java_type.count("[]")
    java_type = java_type.replace("[]", "")

    primitive_map = {
        "void": "V", "boolean": "Z", "byte": "B", "char": "C",
        "short": "S", "int": "I", "float": "F", "long": "J", "double": "D"
    }

    if java_type in primitive_map:
        jvm_type = primitive_map[java_type]
    else:
        internal_name = java_type.replace('.', '/')
        obfuscated_name = class_map.get(internal_name, internal_name)
        jvm_type = f"L{obfuscated_name};"

    return ("[" * array_depth) + jvm_type


def get_method_signature(return_type, params_str, class_map):
    """Generates a method signature in JVM format."""
    params = []
    if params_str:
        for param in params_str.split(','):
            param_type = param.strip().split(' ')[0]
            params.append(convert_java_type_to_jvm(param_type, class_map))

    return_type_jvm = convert_java_type_to_jvm(return_type, class_map)
    return f"({''.join(params)}){return_type_jvm}"


def parse_mappings(input_text):
    """
    Parses the mappings text and converts it into a Python data structure.

    Args:
        input_text (str): The text content of the ProGuard mappings.

    Returns:
        dict: A dictionary representing the parsed mappings.
    """
    data = {"classes": {}}
    class_map = {}

    class_re = re.compile(r'^([\w\.$]+) -> ([\w$]+):$')
    method_re = re.compile(r'^\s+(?:\d+:\d+:)?([\w\.<>$]+)\s+([\w<>$]+)\((.*)\)\s+->\s+([\w<>$]+)$')
    field_re = re.compile(r'^\s+([\w\.<>$]+)\s+([\w$]+)\s+->\s+([\w$]+)$')

    lines = [line.rstrip() for line in input_text.split('\n') if line.strip() and not line.startswith('#')]

    print("First pass: parsing class definitions...")
    for line in lines:
        if class_match := class_re.match(line):
            original_java = class_match.group(1)
            obfuscated_name = class_match.group(2)
            original_jvm = original_java.replace('.', '/')
            class_map[original_jvm] = obfuscated_name
            data["classes"][original_jvm] = {
                "name": obfuscated_name,
                "methods": {},
                "fields": {}
            }
    print(f"Found and parsed {len(class_map)} classes.")

    # Track methods by their original name to handle overloading
    print("Second pass: parsing methods and fields...")
    current_class_jvm = None
    method_overloads = {}  # Temporary storage for method overloads

    for line in lines:
        if class_match := class_re.match(line):
            # Process any pending method overloads for the previous class
            if current_class_jvm and method_overloads:
                for method_name, methods in method_overloads.items():
                    if len(methods) > 1:
                        # Multiple methods with the same name - store as an array
                        data["classes"][current_class_jvm]["methods"][method_name] = methods
                    else:
                        # Only one method with this name - store as a single object
                        data["classes"][current_class_jvm]["methods"][method_name] = methods[0]

                # Clear the overloads for the next class
                method_overloads = {}

            current_class_jvm = class_match.group(1).replace('.', '/')
            continue

        if not current_class_jvm:
            continue

        if method_match := method_re.match(line):
            return_type, method_name, params, obf_method = method_match.groups()
            signature = get_method_signature(return_type, params, class_map)

            # Create a method entry
            method_entry = {
                "name": obf_method,
                "signature": signature
            }

            # Track method overloads by original method name
            if method_name not in method_overloads:
                method_overloads[method_name] = []
            method_overloads[method_name].append(method_entry)

        elif field_match := field_re.match(line):
            field_type, field_name, obf_field = field_match.groups()
            data["classes"][current_class_jvm]["fields"][field_name] = {
                "name": obf_field
            }

    # Process any pending method overloads for the last class
    if current_class_jvm and method_overloads:
        for method_name, methods in method_overloads.items():
            if len(methods) > 1:
                # Multiple methods with the same name - store as an array
                data["classes"][current_class_jvm]["methods"][method_name] = methods
            else:
                # Only one method with this name - store as a single object
                data["classes"][current_class_jvm]["methods"][method_name] = methods[0]

    print("Method and field parsing complete.")
    return data


def main():
    """
    Main function that orchestrates the download, parsing, and saving of mappings.
    """
    # Set up argument parsing
    parser = argparse.ArgumentParser(description='Download and convert Minecraft client mappings.')
    parser.add_argument('--current', action='store_true',
                        help='Automatically download the newest available release version without showing the menu')
    args = parser.parse_args()

    release_versions = get_all_release_versions()
    if not release_versions:
        return

    if args.current:
        # Automatically select the first (newest) version
        selected_version = release_versions[0]
        print(f"Automatically selected the newest version: {selected_version}")
    else:
        # Present the interactive menu as before
        selected_version = present_version_menu(release_versions)
        if not selected_version:
            print("No version selected. Exiting.")
            return

    mappings_text = download_client_mappings(selected_version)
    if not mappings_text:
        return

    print("Converting mappings into the data structure...")
    mapping_data = parse_mappings(mappings_text)

    ordered_data = {"version": selected_version}
    ordered_data.update(mapping_data)

    output_filename = 'mappings.json'
    print(f"Writing output to '{output_filename}'...")
    try:
        with open(output_filename, 'w', encoding='utf-8') as f:
            json.dump(ordered_data, f, indent=4, ensure_ascii=False)
        print("Operation completed successfully!")
        print(f"The file '{output_filename}' has been created/overwritten for version {selected_version}.")
    except IOError as e:
        print(f"Error while writing the JSON file: {e}")


if __name__ == '__main__':
    main()