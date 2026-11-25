#!/bin/sh
while true
do
/opt/python/bin/python3 main.py
echo "If you want to completely stop the process now, press Ctrl+C before
the time is up!"
echo "Rebooting in:"
for i in 5 4 3 2 1
do
echo "$i..."
sleep 1
done
echo "Rebooting now!"
done 
