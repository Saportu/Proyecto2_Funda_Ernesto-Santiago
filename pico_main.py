import sys
import time
import network
import socket
try:
    import uselect as select
except ImportError:
    import select
from machine import Pin, PWM

# ============================================================
#                   CONFIGURACION WIFI
# ============================================================
# IMPORTANTE: la Pico y la PC deben estar en la MISMA red WiFi.
SSID     = "fundamentos"             # <-- nombre exacto de tu hotspot
PASSWORD = "12345678"          # <-- contrasena del hotspot
PUERTO   = 1234                # <-- mismo puerto que pongas en la GUI
# La IP se pide automaticamente (DHCP). Saldra impresa en el Shell.
# ============================================================

# PINES
BOTON_PIN  = 16
BUZZER_PIN = 15
DIP_PIN    = 14
A0_PIN, A1_PIN, A2_PIN, A3_PIN = 2, 3, 4, 5     # 4 bits hacia el circuito
SW_PIN = 6                                        # switch de habilitacion (punto N)

boton    = Pin(BOTON_PIN, Pin.IN, Pin.PULL_UP)
dip      = Pin(DIP_PIN, Pin.IN, Pin.PULL_UP)
led_show = Pin("LED", Pin.OUT)

buzzer = PWM(Pin(BUZZER_PIN))
buzzer.freq(700)
buzzer.duty_u16(0)

a0 = Pin(A0_PIN, Pin.OUT)
a1 = Pin(A1_PIN, Pin.OUT)
a2 = Pin(A2_PIN, Pin.OUT)
a3 = Pin(A3_PIN, Pin.OUT)
sw = Pin(SW_PIN, Pin.IN, Pin.PULL_UP)

# Tabla Morse
MORSE = {
    'A':'.-','B':'-...','C':'-.-.','D':'-..','E':'.','F':'..-.','G':'--.',
    'H':'....','I':'..','J':'.---','K':'-.-','L':'.-..','M':'--','N':'-.',
    'O':'---','P':'.--.','Q':'--.-','R':'.-.','S':'...','T':'-','U':'..-',
    'V':'...-','W':'.--','X':'-..-','Y':'-.--','Z':'--..',
    '0':'-----','1':'.----','2':'..---','3':'...--','4':'....-',
    '5':'.....','6':'-....','7':'--...','8':'---..','9':'----.',
}
MORSE_INV = {v: k for k, v in MORSE.items()}

# AJUSTES
unidad      = 0.2
SHOW_UNIDAD = 0.12
FIN_MS      = 3000
MAX_MS      = 60000

# Estado de la captura
capturando    = False
frase_actual  = ""
letra         = ""
codigos       = []
texto         = ""
prev          = 1
press_start   = 0
ultimo_suelto = None
t_captura     = 0

# ============================================================
#        ENVIO / RECEPCION POR EL SOCKET (en vez de USB)
# ============================================================
_conn = None   # conexion TCP activa con la PC
_rx   = ""     # buffer de bytes recibidos sin procesar

def send_line(s):
    """Manda una linea a la PC por WiFi. Reemplaza a print()."""
    global _conn
    if _conn is None:
        return
    try:
        _conn.send((s + "\n").encode())
    except Exception:
        pass

def leer_lineas(conn, poller):
    """Lee comandos de la PC sin bloquear. Igual que antes, pero por socket.
    Devuelve la lista de lineas completas recibidas."""
    global _rx
    lineas = []
    eventos = poller.poll(0)            # 0 = no esperar
    for s, flag in eventos:
        if flag & (select.POLLHUP | select.POLLERR):
            raise OSError("cliente desconectado")
        if flag & select.POLLIN:
            data = conn.recv(256)
            if not data:                # el cliente cerro la conexion
                raise OSError("cliente desconectado")
            _rx += data.decode("utf-8", "ignore")
    # extraer todas las lineas completas del buffer
    while True:
        idx = -1
        for sep in ("\n", "\r"):
            j = _rx.find(sep)
            if j != -1 and (idx == -1 or j < idx):
                idx = j
        if idx == -1:
            break
        linea = _rx[:idx]
        _rx = _rx[idx + 1:]
        if linea:
            lineas.append(linea)
    return lineas

# ============================================================
#                   CIRCUITO SUMADOR +5
# ============================================================
def bin4(v):
    return "{:04b}".format(v & 0xF)

def set_bits(valor):
    a0.value(valor & 1)
    a1.value((valor >> 1) & 1)
    a2.value((valor >> 2) & 1)
    a3.value((valor >> 3) & 1)

def suma5(valor):
    return (valor + 5) & 0xF

def leer_enable():
    return 1 if sw.value() == 1 else 0

def tono_on():
    buzzer.duty_u16(20000)

def tono_off():
    buzzer.duty_u16(0)

def leer_modo():
    return "sonido" if dip.value() == 0 else "local"

def mostrar_frase(usar_buzzer=False):
    u = SHOW_UNIDAD
    for ch in frase_actual.upper():
        if ch == " ":
            time.sleep(u * 7)
            continue
        codigo = MORSE.get(ch, "")
        for sym in codigo:
            led_show.on()
            if usar_buzzer:
                tono_on()
            time.sleep(u * (3 if sym == "-" else 1))
            led_show.off()
            tono_off()
            time.sleep(u)
        time.sleep(u * 2)

def enviar_morse_live():
    disp = " ".join(codigos + ([letra] if letra else []))
    send_line("MORSE:" + disp)

def _letra_lista(codigo):
    global texto
    L = MORSE_INV.get(codigo, "")
    texto += L
    if L:
        ascii_v = ord(L)
        bits4 = ascii_v & 0xF
        set_bits(bits4)
        send_line("INFO:%s;%d;%s;%s;%d" % (L, ascii_v, bin4(bits4), bin4(suma5(bits4)), leer_enable()))

def iniciar_captura():
    global capturando, letra, codigos, texto, prev, press_start, ultimo_suelto, t_captura
    letra = ""
    codigos = []
    texto = ""
    prev = boton.value()
    press_start = 0
    ultimo_suelto = None
    t_captura = time.ticks_ms()
    set_bits(0)
    capturando = True

def finalizar_captura():
    global capturando, letra
    if letra:
        codigos.append(letra)
        _letra_lista(letra)
        letra = ""
    enviar_morse_live()
    send_line("TEXTO:" + texto)
    capturando = False

def actualizar_captura():
    global prev, press_start, ultimo_suelto, letra
    v = boton.value()
    now = time.ticks_ms()

    if prev == 1 and v == 0:
        press_start = now
        led_show.on()
        time.sleep_ms(15)
    elif prev == 0 and v == 1:
        led_show.off()
        dur = time.ticks_diff(now, press_start)
        letra += "." if dur < int(unidad * 2 * 1000) else "-"
        ultimo_suelto = now
        enviar_morse_live()
        time.sleep_ms(15)

    if v == 1 and letra and ultimo_suelto is not None:
        if time.ticks_diff(now, ultimo_suelto) >= int(unidad * 3 * 1000):
            codigos.append(letra)
            _letra_lista(letra)
            letra = ""
            enviar_morse_live()

    if ultimo_suelto is not None and time.ticks_diff(now, ultimo_suelto) >= FIN_MS:
        finalizar_captura()
    elif time.ticks_diff(now, t_captura) >= MAX_MS:
        finalizar_captura()

    prev = v

def procesar(cmd):
    global unidad, frase_actual
    if cmd == "PING":
        send_line("PONG")
    elif cmd.startswith("FRASE:"):
        frase_actual = cmd[6:]
        send_line("LISTO")
    elif cmd == "SHOW":
        mostrar_frase(usar_buzzer=False)
        send_line("LISTO")
    elif cmd == "PLAY":
        mostrar_frase(usar_buzzer=True)
        send_line("LISTO")
    elif cmd == "LEDS_OFF":
        led_show.off()
        tono_off()
        send_line("LISTO")
    elif cmd.startswith("UNIDAD:"):
        try:
            unidad = float(cmd[7:])
        except Exception:
            pass
        send_line("UNIDAD_OK")
    elif cmd == "MODO":
        send_line("MODO:" + leer_modo())
    elif cmd == "CAPTURAR":
        iniciar_captura()
        send_line("LISTO")
    elif cmd.startswith("BITS:"):
        s = cmd[5:].strip()
        try:
            valor = int(s, 2) & 0xF
        except Exception:
            valor = 0
        set_bits(valor)
        send_line("RES:%s;%s;%d" % (bin4(valor), bin4(suma5(valor)), leer_enable()))
    elif cmd == "ENABLE?":
        send_line("ENABLE:%d" % leer_enable())

# ============================================================
#                   CONEXION WIFI
# ============================================================
def conectar_wifi():
    # Pausa inicial: en arranque en frio el chip WiFi necesita estabilizarse.
    time.sleep(3)
    wlan = network.WLAN(network.STA_IF)

    intento = 0
    while True:                         # reintenta hasta lograrlo
        intento += 1
        print("Intento de conexion #%d a %s ..." % (intento, SSID))
        # Reinicio limpio de la interfaz en cada intento
        try:
            wlan.active(False)
            time.sleep(1)
            wlan.active(True)
            time.sleep(1)
            wlan.connect(SSID, PASSWORD)
        except Exception as e:
            print("Error al iniciar WiFi:", e)

        t = 0
        while not wlan.isconnected() and t < 15000:   # espera hasta 15 s
            led_show.toggle()           # parpadeo = conectando
            time.sleep_ms(250)
            t += 250
        led_show.off()

        if wlan.isconnected():
            ip = wlan.ifconfig()[0]
            print("WiFi OK. IP de la Pico:", ip)
            for _ in range(3):          # 3 parpadeos cortos = conectado
                led_show.on(); time.sleep_ms(80)
                led_show.off(); time.sleep_ms(120)
            return ip

        # No conecto: avisa y reintenta tras una breve pausa
        print("No conecto (intento #%d). Reintentando..." % intento)
        for _ in range(2):              # 2 parpadeos largos = fallo, reintentando
            led_show.on(); time.sleep_ms(400)
            led_show.off(); time.sleep_ms(200)
        time.sleep(2)

# ============================================================
#               ATENDER A LA PC (una conexion)
# ============================================================
def atender_cliente(conn):
    global _conn, _rx, capturando
    _conn = conn
    _rx = ""
    capturando = False
    set_bits(0)
    poller = select.poll()
    poller.register(conn, select.POLLIN)
    send_line("LISTO - Pico conectada por WiFi")
    try:
        while True:
            for linea in leer_lineas(conn, poller):
                procesar(linea.strip())
            if capturando:
                actualizar_captura()
            time.sleep_ms(2)
    except OSError:
        print("PC desconectada.")
    finally:
        try:
            poller.unregister(conn)
        except Exception:
            pass
        _conn = None
        capturando = False
        led_show.off()
        tono_off()

# ============================================================
#                   PROGRAMA PRINCIPAL
# ============================================================
def main():
    set_bits(0)
    ip = conectar_wifi()       # no regresa hasta que haya WiFi

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(socket.getaddrinfo("0.0.0.0", PUERTO)[0][-1])
    s.listen(1)
    print("Servidor TCP escuchando en %s:%d" % (ip, PUERTO))

    while True:
        print("Esperando conexion de la PC...")
        led_show.on()                   # LED fijo = listo, esperando PC
        try:
            conn, cliente = s.accept()
        except Exception as e:
            print("Error en accept:", e)
            continue
        led_show.off()
        print("PC conectada:", cliente)
        atender_cliente(conn)
        try:
            conn.close()
        except Exception:
            pass

main()    