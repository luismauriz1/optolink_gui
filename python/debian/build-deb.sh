#!/bin/bash
# Builds dist/optolink-api_<version>_all.deb on Debian / Raspberry Pi OS.
# Usage: bash debian/build-deb.sh [optolink.xml]   (default: optolink.example.xml)
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
src="$(dirname "$here")"
config="$(realpath "${1:-$src/optolink.example.xml}")"
version="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$src/optolink/__init__.py")"
pkg=optolink-api
root="$(mktemp -d)"
trap 'rm -rf "$root"' EXIT

python3 -m pip wheel --no-deps --wheel-dir "$root/opt/optolink" "$src"

install -D -m 644 "$config" "$root/etc/optolink/optolink.xml"
install -D -m 644 "$here/optolink.default" "$root/etc/default/optolink"
install -D -m 644 "$here/optolink.service" "$root/lib/systemd/system/optolink.service"
install -D -m 644 "$src/README.md" "$root/usr/share/doc/$pkg/README.md"

mkdir -p "$root/DEBIAN"
for script in postinst prerm postrm; do
    install -m 755 "$here/$script" "$root/DEBIAN/$script"
done
printf '%s\n' /etc/optolink/optolink.xml /etc/default/optolink > "$root/DEBIAN/conffiles"
cat > "$root/DEBIAN/control" <<EOF
Package: $pkg
Version: $version
Architecture: all
Maintainer: Optolink API <optolink@localhost>
Depends: python3 (>= 3.9), python3-venv, adduser
Section: misc
Priority: optional
Description: REST API with Swagger UI for Viessmann heating controllers
 Reads and writes values of Viessmann (Vitotronic) heating controllers via
 the Optolink interface and provides them as REST/JSON API.
 Python dependencies are installed into /opt/optolink/venv (needs internet).
EOF

mkdir -p "$src/dist"
dpkg-deb --root-owner-group --build "$root" "$src/dist/${pkg}_${version}_all.deb"
