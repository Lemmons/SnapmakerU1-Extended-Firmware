#!/bin/bash
# Patch workflow helper - assists in creating sequential patches for overlays
#
# This script helps manage the complexity of creating patches that build on top of
# previous patches by maintaining incremental states.

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
WORK_DIR="$ROOT_DIR/tmp/patch-work"
FIRMWARE_DIR="$ROOT_DIR/tmp/extracted"

show_help() {
  cat <<EOF
Patch Workflow Helper

Usage: $0 <command> [args]

Commands:
  init <overlay-name>
      Initialize a patch workspace for an overlay.
      Extracts the original rootfs and creates a baseline.

  apply-all <overlay-name>
      Apply all existing patches from the overlay in order to create
      the current state. Use this before creating a new patch.

  save <overlay-name> <patch-name> <files...>
      Save a new patch with changes to the specified files.
      Example: save rfid-support 06-add-feature home/lava/klipper/klippy/extras/file.py

  diff <overlay-name> <files...>
      Show diff of current changes without saving.

  reset <overlay-name>
      Reset the workspace to the state after applying all existing patches.

  clean
      Remove the entire patch workspace.

  status <overlay-name>
      Show what files have been modified in the current workspace.

Examples:
  # Start working on a new patch for an existing overlay
  $0 init firmware-extended/13-rfid-support
  $0 apply-all firmware-extended/13-rfid-support

  # Edit files directly in the workspace
  vim tmp/patch-work/current/home/lava/klipper/klippy/extras/filament_detect.py

  # Check what changed
  $0 status firmware-extended/13-rfid-support
  $0 diff firmware-extended/13-rfid-support home/lava/klipper/klippy/extras/filament_detect.py

  # Save as a new patch
  $0 save firmware-extended/13-rfid-support 06-my-new-feature home/lava/klipper/klippy/extras/filament_detect.py

EOF
}

init_workspace() {
  local overlay_name="$1"

  if [[ -z "$overlay_name" ]]; then
    echo "Error: overlay name required"
    echo "Usage: $0 init <overlay-name>"
    exit 1
  fi

  if [[ ! -d "$ROOT_DIR/overlays/$overlay_name" ]]; then
    echo "Error: Overlay directory not found: overlays/$overlay_name"
    exit 1
  fi

  echo ">> Initializing patch workspace for $overlay_name..."

  # Create workspace directories
  mkdir -p "$WORK_DIR"

  # Check if we have the extracted firmware
  if [[ ! -d "$FIRMWARE_DIR/rk-unpacked" ]]; then
    echo "Error: No extracted firmware found at tmp/extracted/"
    echo "Please run 'make extract' first to extract the base firmware"
    exit 1
  fi

  # Extract original baseline (if not already done)
  if [[ ! -d "$WORK_DIR/original" ]]; then
    echo ">> Extracting original rootfs..."
    unsquashfs -d "$WORK_DIR/original" "$FIRMWARE_DIR/rk-unpacked/rootfs.img"
  fi

  # Extract current working copy
  echo ">> Creating working copy..."
  rm -rf "$WORK_DIR/current"
  unsquashfs -d "$WORK_DIR/current" "$FIRMWARE_DIR/rk-unpacked/rootfs.img"

  # Save overlay name
  echo "$overlay_name" > "$WORK_DIR/.overlay"

  echo ">> Workspace initialized at $WORK_DIR"
  echo "   Original: $WORK_DIR/original"
  echo "   Working:  $WORK_DIR/current"
}

apply_all_patches() {
  local overlay_name="$1"

  if [[ -z "$overlay_name" ]]; then
    if [[ -f "$WORK_DIR/.overlay" ]]; then
      overlay_name=$(cat "$WORK_DIR/.overlay")
    else
      echo "Error: overlay name required"
      exit 1
    fi
  fi

  local patch_dir="$ROOT_DIR/overlays/$overlay_name/patches"

  if [[ ! -d "$patch_dir" ]]; then
    echo "No patches directory found for $overlay_name"
    return
  fi

  echo ">> Applying all patches from $overlay_name..."

  # Reset to clean state first
  echo "   Resetting to clean baseline..."
  rm -rf "$WORK_DIR/current"
  unsquashfs -d "$WORK_DIR/current" "$FIRMWARE_DIR/rk-unpacked/rootfs.img"

  # Run pre-scripts if they exist
  local overlay_dir="$ROOT_DIR/overlays/$overlay_name"
  if [[ -d "$overlay_dir/pre-scripts" ]]; then
    for scriptfile in "$overlay_dir/pre-scripts/"*.sh; do
      if [[ -f "$scriptfile" ]]; then
        echo "   Running pre-script: $(basename "$scriptfile")"
        bash "$scriptfile" "$WORK_DIR/current"
      fi
    done
  fi

  # Apply patches in alphabetical order
  local count=0
  for patch_file in "$patch_dir"/*.patch; do
    if [[ ! -f "$patch_file" ]]; then
      continue
    fi

    echo "   Applying $(basename "$patch_file")..."

    # Apply patch - convert rootfs.original to our paths
    set +e
    sed "s|rootfs\\.original/|original/|g; s|rootfs/|current/|g" "$patch_file" | \
      patch -d "$WORK_DIR" -p0 2>&1
    patch_result=$?
    set -e

    if [[ $patch_result -ne 0 ]]; then
      echo "Error: Failed to apply patch $(basename "$patch_file") (exit code: $patch_result)"
      echo "You may need to fix conflicts manually in $WORK_DIR/current"
      exit 1
    fi

    count=$((count + 1))
    echo "   ✓ Patch $(basename "$patch_file") applied successfully"
  done

  echo ">> Applied $count patches"
  echo ">> You can now edit files in: $WORK_DIR/current"
}

show_status() {
  local overlay_name="${1:-$(cat "$WORK_DIR/.overlay" 2>/dev/null)}"

  if [[ ! -d "$WORK_DIR/current" ]]; then
    echo "No workspace initialized. Run: $0 init <overlay-name>"
    exit 1
  fi

  echo ">> Changed files in workspace:"
  cd "$WORK_DIR"

  # Find all different files
  diff -qr original current | grep "Files.*differ" | sed 's|Files original/||; s| and current/.*||' || echo "   (no changes)"
}

show_diff() {
  local overlay_name="${1:-$(cat "$WORK_DIR/.overlay" 2>/dev/null)}"
  shift || true

  if [[ ! -d "$WORK_DIR/current" ]]; then
    echo "No workspace initialized. Run: $0 init <overlay-name>"
    exit 1
  fi

  cd "$WORK_DIR"

  if [[ $# -eq 0 ]]; then
    # Show all diffs
    diff -Nur original current || true
  else
    # Show diffs for specific files
    for file in "$@"; do
      diff -Nur "original/$file" "current/$file" || true
    done
  fi
}

save_patch() {
  local overlay_name="${1:-$(cat "$WORK_DIR/.overlay" 2>/dev/null)}"
  local patch_name="$2"
  shift 2

  if [[ -z "$patch_name" ]]; then
    echo "Error: patch name required"
    echo "Usage: $0 save <overlay-name> <patch-name> <files...>"
    exit 1
  fi

  if [[ $# -eq 0 ]]; then
    echo "Error: at least one file path required"
    echo "Usage: $0 save <overlay-name> <patch-name> <files...>"
    exit 1
  fi

  local patch_dir="$ROOT_DIR/overlays/$overlay_name/patches"
  mkdir -p "$patch_dir"

  local patch_file="$patch_dir/$patch_name.patch"

  echo ">> Creating patch: $patch_name.patch"

  cd "$WORK_DIR"

  # Generate the patch
  {
    for file in "$@"; do
      if [[ -f "current/$file" ]] || [[ -f "original/$file" ]]; then
        echo "   Including: $file"
        diff -Nur "original/$file" "current/$file" || true
      else
        echo "   Warning: File not found: $file"
      fi
    done
  } > "$patch_file"

  # Replace our workspace paths with the standard patch paths
  sed -i.bak "s|original/|rootfs.original/|g; s|current/|rootfs/|g" "$patch_file"
  rm "$patch_file.bak"

  echo ">> Patch saved to: $patch_file"
  echo ">> Lines in patch: $(wc -l < "$patch_file")"

  # Now update the baseline for the next patch
  echo ">> Updating baseline (original) to include this patch..."
  for file in "$@"; do
    if [[ -f "current/$file" ]]; then
      mkdir -p "$(dirname "original/$file")"
      cp "current/$file" "original/$file"
    fi
  done

  echo ">> Done! You can continue editing or run 'apply-all' to reset to all patches."
}

reset_workspace() {
  local overlay_name="${1:-$(cat "$WORK_DIR/.overlay" 2>/dev/null)}"

  echo ">> Resetting workspace..."
  apply_all_patches "$overlay_name"
}

clean_workspace() {
  echo ">> Cleaning patch workspace..."
  rm -rf "$WORK_DIR"
  echo ">> Done"
}

# Main command dispatch
case "${1:-help}" in
  init)
    init_workspace "$2"
    ;;
  apply-all)
    apply_all_patches "$2"
    ;;
  save)
    shift
    save_patch "$@"
    ;;
  diff)
    shift
    show_diff "$@"
    ;;
  status)
    show_status "$2"
    ;;
  reset)
    reset_workspace "$2"
    ;;
  clean)
    clean_workspace
    ;;
  help|--help|-h)
    show_help
    ;;
  *)
    echo "Unknown command: $1"
    show_help
    exit 1
    ;;
esac
