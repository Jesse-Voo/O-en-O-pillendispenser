#!/bin/bash
# ============================================================
#  Thelma – Eenmalige setup op de Raspberry Pi
#  Pad: /home/leerling/O-O-pillendispenser/thelma.py
# ============================================================

set -e

THELMA_PATH="/home/leerling/O-O-pillendispenser/thelma.py"

echo ""
echo "=== [1/4] Tkinter installeren ==="
sudo apt-get update -qq
sudo apt-get install -y python3-tk

echo ""
echo "=== [2/4] Autologin naar desktop inschakelen ==="
sudo raspi-config nonint do_boot_behaviour B4

echo ""
echo "=== [3/4] Schermbeveiliging uitzetten ==="
mkdir -p ~/.config/lxsession/LXDE-pi
cat > ~/.config/lxsession/LXDE-pi/autostart << EOF
@xset s off
@xset -dpms
@xset s noblank
EOF

echo ""
echo "=== [4/4] Autostart aanmaken ==="
mkdir -p ~/.config/autostart
cat > ~/.config/autostart/thelma.desktop << EOF
[Desktop Entry]
Type=Application
Name=Thelma
Exec=python3 $THELMA_PATH
X-GNOME-Autostart-enabled=true
EOF

echo ""
echo "================================================"
echo "  Klaar! Start opnieuw op met: sudo reboot"
echo "  Thelma start dan automatisch fullscreen."
echo ""
echo "  Stoppen:  pkill python3"
echo "  SSH:      ssh leerling@10.42.0.1"
echo "================================================"
echo ""
