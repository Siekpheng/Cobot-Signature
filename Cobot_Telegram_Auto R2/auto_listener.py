import os
import re
import math
import struct
import socket
import json
import urllib.request
import urllib.parse
import time

# --- TELEGRAM BOT CONFIG ---
BOT_TOKEN = "8101041941:AAGKYlyJ6s4-BzkQL6q6DQj5VnRFu0SilBI"

# --- ROBOT CONFIGURATION ---
ROBOT_IP = '192.168.31.116'
CMD_PORT = 5000

BASE_X = 143.06
BASE_Y = -605.66
BASE_Z = 370.00  # Desk height (Paper)
RX = 0.10
RY = 0.10
RZ = 19.92
SIGNATURE_SCALE = 0.20  # 20% scale

def send_and_wait(sock, cmd_str):
    """Reliable wait for big moves (home, escape). Uses 2.5s timeout."""
    print(f"Sending: {cmd_str[:60]}")
    sock.sendall((cmd_str + '\n').encode('utf-8'))
    buffer = ""; command_accepted = False; motion_started = False
    while True:
        sock.settimeout(2.5 if command_accepted and not motion_started else 30.0)
        try:
            chunk = sock.recv(1024).decode('utf-8', errors='replace')
            if not chunk: break
            buffer += chunk
            lines = buffer.split('\n'); buffer = lines[-1]
            for line in lines[:-1]:
                line = line.strip()
                if line: print(f"  ROBOT: {line}")
                if 'command was executed' in line.lower(): command_accepted = True
                if 'motion_changed][300]' in line or 'motion_changed][400]' in line: motion_started = True
                if 'motion_changed][0]' in line and motion_started: return
        except socket.timeout:
            if command_accepted: return

def send_draw(sock, cmd_str):
    """Fast send for drawing points. Uses 0.3s timeout like R1."""
    sock.sendall((cmd_str + '\n').encode('utf-8'))
    buffer = ""; command_accepted = False; motion_started = False
    while True:
        sock.settimeout(0.3 if command_accepted and not motion_started else 30.0)
        try:
            chunk = sock.recv(1024).decode('utf-8', errors='replace')
            if not chunk: break
            buffer += chunk
            lines = buffer.split('\n'); buffer = lines[-1]
            for line in lines[:-1]:
                line = line.strip()
                if 'command was executed' in line.lower(): command_accepted = True
                if 'motion_changed][300]' in line or 'motion_changed][400]' in line: motion_started = True
                if 'motion_changed][0]' in line and motion_started: return
        except socket.timeout:
            if command_accepted and not motion_started: return
            elif motion_started: return

def run_signature_on_robot(gcode_text, filename):
    print(f"Connecting to Rainbow Cobot at {ROBOT_IP}...")
    try:
        s_cmd = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s_cmd.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        s_cmd.settimeout(5.0)
        s_cmd.connect((ROBOT_IP, CMD_PORT))
    except Exception as e:
        print(f"[ERROR] Failed to connect to Robot: {e}")
        return False

    cur_x, cur_y = 0.0, 0.0
    strokes = []
    current_stroke = []

    for line in gcode_text.split('\n'):
        l = line.strip().upper()
        if not l or l.startswith(';') or l.startswith('('): continue
        if l.startswith('G00') or l.startswith('G01') or l.startswith('G0 ') or l.startswith('G1 '):
            x_match = re.search(r'X([-\d.]+)', l)
            y_match = re.search(r'Y([-\d.]+)', l)
            if x_match: cur_x = float(x_match.group(1))
            if y_match: cur_y = float(y_match.group(1))
            is_travel = 'G00' in l or 'G0 ' in l
            if is_travel:
                if current_stroke: strokes.append(current_stroke)
                current_stroke = []
                strokes.append({'travel': True, 'x': cur_x, 'y': cur_y})
            else:
                if not current_stroke:
                    current_stroke.append({'x': cur_x, 'y': cur_y})
                else:
                    last_pt = current_stroke[-1]
                    dist = math.hypot(cur_x - last_pt['x'], cur_y - last_pt['y'])
                    if dist >= 2.0:
                        current_stroke.append({'x': cur_x, 'y': cur_y})

    if current_stroke:
        strokes.append(current_stroke)

    try:
        print(f"Executing: {filename}")

        # Safe Home Configuration (Hovering 80mm above paper at Z=450)
        home_cmd = "jointall 0.5, 0.5, 90.00, -16.72, -75.61, -87.79, 70.01, 180.18"
        send_and_wait(s_cmd, home_cmd)

        for item in strokes:
            if isinstance(item, dict) and item.get('travel'):
                continue
            else:
                pts = item
                spd = 0.8; acc = 0.4
                safe_z = 450.00

                # Air travel to stroke start
                start_pt = pts[0]
                cmd = 'movetcp %.2f, %.2f, %.3f, %.3f, %.3f, %.3f, %.3f, %.3f' % (
                    spd, acc,
                    BASE_X - (start_pt['x'] * SIGNATURE_SCALE),
                    BASE_Y - (start_pt['y'] * SIGNATURE_SCALE),
                    safe_z, RX, RY, RZ)
                send_and_wait(s_cmd, cmd)

                # Plunge pen straight down to paper (must wait for full 50mm move!)
                cmd = 'movetcp %.2f, %.2f, %.3f, %.3f, %.3f, %.3f, %.3f, %.3f' % (
                    spd, acc,
                    BASE_X - (start_pt['x'] * SIGNATURE_SCALE),
                    BASE_Y - (start_pt['y'] * SIGNATURE_SCALE),
                    BASE_Z, RX, RY, RZ)
                send_and_wait(s_cmd, cmd)

                # Draw points (fast 0.3s wait for tiny horizontal lines)
                draw_z = BASE_Z
                for pt in pts[1:]:
                    cmd = 'movetcp %.2f, %.2f, %.3f, %.3f, %.3f, %.3f, %.3f, %.3f' % (
                        spd, acc,
                        BASE_X - (pt['x'] * SIGNATURE_SCALE),
                        BASE_Y - (pt['y'] * SIGNATURE_SCALE),
                        draw_z, RX, RY, RZ)
                    send_draw(s_cmd, cmd)

                # Lift pen
                last_pt = pts[-1]
                cmd = 'movetcp %.2f, %.2f, %.3f, %.3f, %.3f, %.3f, %.3f, %.3f' % (
                    spd, acc,
                    BASE_X - (last_pt['x'] * SIGNATURE_SCALE),
                    BASE_Y - (last_pt['y'] * SIGNATURE_SCALE),
                    safe_z, RX, RY, RZ)
                send_and_wait(s_cmd, cmd)

        # Return to safe home
        send_and_wait(s_cmd, home_cmd)
        print("Finished drawing!")
    except Exception as e:
        print(f"Error during robot execution: {e}")
        return False
    finally:
        s_cmd.close()

    return True

def send_telegram_reply(chat_id, text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = json.dumps({'chat_id': chat_id, 'text': text}).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
    try:
        urllib.request.urlopen(req)
    except Exception:
        pass

def listen_for_signatures():
    url = "https://ntfy.sh/cobot_sig_relay_R2_572910/json"
    print("Connecting to live relay...")
    
    while True:
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=86400) as response:
                print("Listening for incoming signatures from GitHub...")
                for line in response:
                    if not line.strip():
                        continue
                    data = json.loads(line.decode('utf-8'))
                    
                    if data.get('event') == 'message' and 'attachment' in data:
                        attachment_url = data['attachment']['url']
                        filename = data['attachment']['name']
                        
                        print(f"\n[!] INCOMING SIGNATURE: {filename}")
                        
                        # Notify user on Telegram that robot is starting
                        send_telegram_reply("1466832113", f"✅ Received {filename}! Robot 2 is starting to draw...")
                        
                        # Download G-code from relay
                        try:
                            with urllib.request.urlopen(attachment_url) as f:
                                gcode_text = f.read().decode('utf-8')
                                
                            success = run_signature_on_robot(gcode_text, filename)
                            
                            if success:
                                send_telegram_reply("1466832113", "🎯 Robot 2 finished drawing successfully!")
                            else:
                                send_telegram_reply("1466832113", "❌ Error: Robot 2 is offline or disconnected.")
                        except Exception as e:
                            print(f"Error downloading file: {e}")
                            
        except Exception as e:
            print(f"Relay connection dropped, reconnecting in 3 seconds... ({e})")
            time.sleep(3)

if __name__ == '__main__':
    print("==========================================================")
    print("   AUTO SIGNATURE RECEIVER RUNNING (Relay Mode)           ")
    print("==========================================================")
    print("Leave this window open. When someone signs on GitHub,")
    print("the robot will automatically wake up and draw it!")
    print("==========================================================\n")
    listen_for_signatures()
