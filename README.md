sudo cp /home/leerling/O-O-pillendispenser/thelma.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable thelma
sudo systemctl start thelma
