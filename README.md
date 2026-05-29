# USB gadget mode aanzetten
echo "dtoverlay=dwc2" | sudo tee -a /boot/firmware/config.txt
sudo sed -i 's/rootwait/rootwait modules-load=dwc2,g_ether/' /boot/firmware/cmdline.txt

# Statisch IP op USB-interface
sudo nmcli con add type ethernet ifname usb0 con-name "usb0-static" \
  ipv4.method manual ipv4.addresses "192.168.55.1/24" \
  connection.autoconnect yes


sudo reboot
