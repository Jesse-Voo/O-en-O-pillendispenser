# USB gadget mode aanzetten
echo "dtoverlay=dwc2" | sudo tee -a /boot/firmware/config.txt
sudo sed -i 's/rootwait/rootwait modules-load=dwc2,g_ether/' /boot/firmware/cmdline.txt

# Statisch IP op USB-interface
sudo tee /etc/network/interfaces.d/usb0 << 'EOF'
auto usb0
iface usb0 inet static
  address 192.168.55.1
  netmask 255.255.255.0
EOF

sudo reboot
