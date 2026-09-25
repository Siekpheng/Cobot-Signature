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
ROBOT_IP = '192.168.31.115'
CMD_PORT = 5000

BASE_X = -65.0
BASE_Y = -525.0
BASE_Z = 368.0
RX = 0.0
RY = -90.0
RZ = 0.0
SIGNATURE_SCALE = 0.25

def send_and_wait(sock, cmd_str):
    print(f"Sending: {cmd_str}")
    sock.sendall((cmd_str + '\n').encode('utf-8'))

    buffer = ""
    command_accepted = False
    motion_started = False

    while True:
        if command_accepted and not motion_started:
            sock.settimeout(0.3)
        else:
            sock.settimeout(30.0)

        try:
            chunk = sock.recv(1024).decode('utf-8', errors='replace')
            if not chunk:
                break
            buffer += chunk
            lines = buffer.split('\n')
            buffer = lines[-1]
            for line in lines[:-1]:
                line = line.strip()
                if line:
                    pass # Silenced robot output for cleaner terminal
                if 'command was executed' in line.lower():
                    command_accepted = True
                if 'motion_changed][300]' in line or 'motion_changed][400]' in line:
                    motion_started = True
                if 'motion_changed][0]' in line and motion_started:
                    return
        except socket.timeout:
            if command_accepted and not motion_started:
                return
            elif motion_started:
                return

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
        if not l or l.startswith(';') or l.startswith('('):
            continue

        if l.startswith('G00') or l.startswith('G01') or l.startswith('G0 ') or l.startswith('G1 '):
            x_match = re.search(r'X([-\d.]+)', l)
            y_match = re.search(r'Y([-\d.]+)', l)

            if x_match: cur_x = float(x_match.group(1))
            if y_match: cur_y = float(y_match.group(1))

            is_travel = 'G00' in l or 'G0 ' in l

            if is_travel:
                if current_stroke:
                    strokes.append(current_stroke)
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
        home_cmd = "jointall -1, -1, -90, -0.82, 108.72, 72.10, -90, -0.01"
        send_and_wait(s_cmd, home_cmd)
        
        for item in strokes:
            if isinstance(item, dict) and item.get('travel'):
                continue 
            else:
                pts = item
                spd = 0.8; acc = 0.4
                safe_z = BASE_Z + 8.0
                
                # Air travel
                start_pt = pts[0]
                cmd = 'movetcp %.2f, %.2f, %.3f, %.3f, %.3f, %.1f, %.1f, %.1f' % (
                    spd, acc, 
                    BASE_X - (start_pt['x'] * SIGNATURE_SCALE), 
                    BASE_Y - (start_pt['y'] * SIGNATURE_SCALE), 
                    safe_z, RX, RY, RZ)
                send_and_wait(s_cmd, cmd)

                # Draw
                draw_z = BASE_Z
                for pt in pts:
                    cmd = 'movetcp %.2f, %.2f, %.3f, %.3f, %.3f, %.1f, %.1f, %.1f' % (
                        spd, acc, 
                        BASE_X - (pt['x'] * SIGNATURE_SCALE), 
                        BASE_Y - (pt['y'] * SIGNATURE_SCALE), 
                        draw_z, RX, RY, RZ)
                    send_and_wait(s_cmd, cmd)
                    
                # Lift
                last_pt = pts[-1]
                cmd = 'movetcp %.2f, %.2f, %.3f, %.3f, %.3f, %.1f, %.1f, %.1f' % (
                    spd, acc, 
                    BASE_X - (last_pt['x'] * SIGNATURE_SCALE), 
                    BASE_Y - (last_pt['y'] * SIGNATURE_SCALE), 
                    safe_z, RX, RY, RZ)
                send_and_wait(s_cmd, cmd)
                    
        send_and_wait(s_cmd, "jointall -1, -1, -90, -0.82, 108.72, 72.10, -90, -0.01")
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
    url = "https://ntfy.sh/cobot_sig_relay_847291/json"
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
                        send_telegram_reply("1466832113", f"✅ Received {filename}! Robot is starting to draw...")
                        
                        # Download G-code from relay
                        try:
                            with urllib.request.urlopen(attachment_url) as f:
                                gcode_text = f.read().decode('utf-8')
                                
                            success = run_signature_on_robot(gcode_text, filename)
                            
                            if success:
                                send_telegram_reply("1466832113", "🎯 Robot finished drawing successfully!")
                            else:
                                send_telegram_reply("1466832113", "❌ Error: Robot is offline or disconnected.")
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
