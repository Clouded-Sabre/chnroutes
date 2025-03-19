#!/usr/bin/env python3

import re
import urllib.request
import sys
import argparse
import math
import textwrap
import time
from collections import deque

def print_step(message):
    print(f"[INFO] {message}")

def generate_ovpn(metric):
    print_step("Generating OpenVPN routing rules...")
    results = fetch_ip_data()
    with open('routes.txt', 'w') as rfile:
        for ip, mask, _ in results:
            route_item = "route {} {} net_gateway {}\n".format(ip, mask, metric)
            rfile.write(route_item)
    print("Usage: Append the content of the newly created routes.txt to your openvpn config file, "
          "and also add 'max-routes {}', which takes a line, to the head of the file.".format(len(results) + 20))

def generate_linux(metric):
    print_step("Generating Linux routing rules...")
    results = fetch_ip_data()
    upscript_header = textwrap.dedent("""\
    #!/bin/bash
    export PATH="/bin:/sbin:/usr/sbin:/usr/bin"
    
    OLDGW=$(dig +short oldgateway)
    
    if [[ $OLDGW == '' ]]; then
        exit 0
    fi
    
    if [ ! -e /tmp/vpn_oldgw ]; then
        echo $OLDGW > /tmp/vpn_oldgw
    fi
    
    """)
    downscript_header = textwrap.dedent("""\
    #!/bin/bash
    export PATH="/bin:/sbin:/usr/sbin:/usr/bin"
    
    OLDGW=`cat /tmp/vpn_oldgw`
    
    """)
    with open('ip-pre-up.sh', 'w') as upfile, open('ip-down.sh', 'w') as downfile:
        upfile.write(upscript_header + '\n')
        downfile.write(downscript_header + '\n')
        for ip, _, mask in results:
            upfile.write("ip route add {}/{} via $OLDGW\n".format(ip, mask))
            downfile.write("ip route del {}/{}\n".format(ip, mask))
        downfile.write("rm /tmp/vpn_oldgw\n")
    print("For pptp only, please copy the file ip-pre-up to the folder /etc/ppp, "
          "and copy the file ip-down to the folder /etc/ppp/ip-down.d.")

def generate_mac(metric):
    print_step("Generating macos routing rules...")
    results = fetch_ip_data()
    upscript_header = textwrap.dedent("""\
    #!/bin/sh
    export PATH="/bin:/sbin:/usr/sbin:/usr/bin"
    
    OLDGW=`netstat -nr | grep '^default' | grep -v 'ppp' | sed 's/default *\\([0-9\\.]*\\) .*/\\1/' | awk '{if($1){print $1}}'`

    if [ ! -e /tmp/pptp_oldgw ]; then
        echo "${OLDGW}" > /tmp/pptp_oldgw
    fi
    
    dscacheutil -flushcache

    route add 10.0.0.0/8 "${OLDGW}"
    route add 172.16.0.0/12 "${OLDGW}"
    route add 192.168.0.0/16 "${OLDGW}"
    """)
    downscript_header = textwrap.dedent("""\
    #!/bin/sh
    export PATH="/bin:/sbin:/usr/sbin:/usr/bin"
    
    if [ ! -e /tmp/pptp_oldgw ]; then
        exit 0
    fi
    
    OLDGW=`cat /tmp/pptp_oldgw`

    route delete 10.0.0.0/8 "${OLDGW}"
    route delete 172.16.0.0/12 "${OLDGW}"
    route delete 192.168.0.0/16 "${OLDGW}"
    """)
    with open('ip-up', 'w') as upfile, open('ip-down', 'w') as downfile:
        upfile.write(upscript_header + '\n')
        downfile.write(downscript_header + '\n')
        for ip, _, mask in results:
            upfile.write('route add {}/{} "${{OLDGW}}"\n'.format(ip, mask))
            downfile.write('route delete {}/{} $OLDGW\n'.format(ip, mask))
        downfile.write("\n\nrm /tmp/pptp_oldgw\n")
    print("For pptp on mac only, please copy ip-up and ip-down to the /etc/ppp folder, "
          "don't forget to make them executable with the chmod command.")

def generate_android(metric):
    print_step("Generating Android routing rules...")
    results = fetch_ip_data()
    upscript_header = textwrap.dedent("""\
    #!/bin/sh
    alias nestat='/system/xbin/busybox netstat'
    alias grep='/system/xbin/busybox grep'
    alias awk='/system/xbin/busybox awk'
    alias route='/system/xbin/busybox route'
    
    OLDGW=`netstat -rn | grep ^0\\.0\\.0\\.0 | awk '{print $2}'`
    
    """)
    downscript_header = textwrap.dedent("""\
    #!/bin/sh
    alias route='/system/xbin/busybox route'
    
    """)
    with open('vpnup.sh', 'w') as upfile, open('vpndown.sh', 'w') as downfile:
        upfile.write(upscript_header + '\n')
        downfile.write(downscript_header + '\n')
        for ip, mask, _ in results:
            upfile.write("route add -net {} netmask {} gw $OLDGW\n".format(ip, mask))
            downfile.write("route del -net {} netmask {}\n".format(ip, mask))
    print("Old school way to call up/down script from openvpn client. "
          "Use the regular openvpn 2.1 method to add routes if it's possible")

def fetch_ip_data():
    print_step("Fetching data from apnic.net, it might take a few minutes, please wait...")
    url = r'http://ftp.apnic.net/apnic/stats/apnic/delegated-apnic-latest'
    with urllib.request.urlopen(url) as response:
        total_size = response.getheader('Content-Length')
        total_size = int(total_size) if total_size else None

        downloaded = 0
        block_size = 8192
        history = deque()  # Stores tuples of (timestamp, cumulative_bytes)
        chunks = []

        # Print initial two lines for progress and progress bar.
        print("Downloading: 0.00% at 0.00 MB/s")
        print("[" + "-" * 50 + "]")

        while True:
            chunk = response.read(block_size)
            if not chunk:
                break
            chunks.append(chunk)
            downloaded += len(chunk)
            current_time = time.time()
            history.append((current_time, downloaded))
            # Remove entries older than 10 seconds
            while history and (current_time - history[0][0]) > 10:
                history.popleft()
            # Calculate average speed over the last 10 seconds
            if history and (current_time - history[0][0]) > 0:
                time_diff = current_time - history[0][0]
                bytes_diff = downloaded - history[0][1]
                speed = bytes_diff / time_diff  # bytes per second
                speed_mb_s = speed / (1024 * 1024)
            else:
                speed_mb_s = 0.0
            progress = (downloaded / total_size * 100) if total_size else 0
            bar_width = 50
            filled_length = int(bar_width * downloaded / total_size) if total_size else 0
            bar = "#" * filled_length + "-" * (bar_width - filled_length)

            # Move the cursor up two lines, clear them, and update
            sys.stdout.write("\033[2A")    # Move cursor up 2 lines
            sys.stdout.write("\033[K")      # Clear current line
            sys.stdout.write("Downloading: {:.2f}% at {:.2f} MB/s\n".format(progress, speed_mb_s))
            sys.stdout.write("\033[K")      # Clear current line
            sys.stdout.write("[" + bar + "]\n")
            sys.stdout.flush()
        data = b"".join(chunks)
    print("")  # Ensure we move to a new line after finishing
    data = data.decode('utf-8')
    cnregex = re.compile(r'apnic\|cn\|ipv4\|[0-9\.]+\|[0-9]+\|[0-9]+\|a.*', re.IGNORECASE)
    cndata = cnregex.findall(data)
    results = []
    for item in cndata:
        unit_items = item.split('|')
        starting_ip = unit_items[3]
        num_ip = int(unit_items[4])
        imask = 0xffffffff ^ (num_ip - 1)
        imask = hex(imask)[2:]
        imask = imask.zfill(8)
        mask = [imask[i:i+2] for i in range(0, 8, 2)]
        mask = [int(i, 16) for i in mask]
        mask = "{}.{}.{}.{}".format(*mask)
        mask2 = 32 - int(math.log(num_ip, 2))
        results.append((starting_ip, mask, mask2))
    print_step("Data parsing completed successfully.")
    return results

if __name__ == '__main__':
    print_step("Starting script execution...")
    parser = argparse.ArgumentParser(description="Generate routing rules for vpn.")
    parser.add_argument('-p', '--platform',
                        dest='platform',
                        default='openvpn',
                        nargs='?',
                        help="Target platforms, it can be openvpn, mac, linux, win, android. openvpn by default.")
    parser.add_argument('-m', '--metric',
                        dest='metric',
                        default=5,
                        nargs='?',
                        type=int,
                        help="Metric setting for the route rules")
    args = parser.parse_args()
    platform = args.platform.lower()
    if platform == 'openvpn':
        generate_ovpn(args.metric)
    elif platform == 'linux':
        generate_linux(args.metric)
    elif platform == 'mac':
        generate_mac(args.metric)
    elif platform == 'win':
        generate_win(args.metric)
    elif platform == 'android':
        generate_android(args.metric)
    else:
        sys.stderr.write("Platform {} is not supported.\n".format(args.platform))
        sys.exit(1)

    print_step("Script execution finished.")
