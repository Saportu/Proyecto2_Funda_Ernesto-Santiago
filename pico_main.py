import sys
import time
from machine import Pin, PWM
try:
    import uselect as select
except ImportError:
    import select

# PINES 
BOTON_PIN  = 16
BUZZER_PIN = 15
DIP_PIN    = 14
# Circuito sumador +5:
A0_PIN, A1_PIN, A2_PIN, A3_PIN = 2, 3, 4, 5     # 4 bits hacia el circuito
SW_PIN = 6                                       # switch de habilitacion (punto N)


boton    = Pin(BOTON_PIN, Pin.IN, Pin.PULL_UP)
dip      = Pin(DIP_PIN, Pin.IN, Pin.PULL_UP)
led_show = Pin("LED", Pin.OUT)

buzzer = PWM(Pin(BUZZER_PIN))
buzzer.freq(700)
buzzer.duty_u16(0)

# Salidas de los 4 bits hacia el circuito
a0 = Pin(A0_PIN, Pin.OUT)
a1 = Pin(A1_PIN, Pin.OUT)
a2 = Pin(A2_PIN, Pin.OUT)
a3 = Pin(A3_PIN, Pin.OUT)
# Switch de habilitacion (con pull-up interno: abierto=1=apagado, cerrado=0=encendido)
sw = Pin(SW_PIN, Pin.IN, Pin.PULL_UP)

# Tabla Morse: letra -> codigo, y la inversa codigo -> letra
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

# Lectura de comandos sin bloquear 
spoll = select.poll()
spoll.register(sys.stdin, select.POLLIN)
buf_in = ""

def leer_lineas():
    global buf_in
    lineas = []
    while spoll.poll(0):
        ch = sys.stdin.read(1)
        if ch in ("\n", "\r"):
            if buf_in:
                lineas.append(buf_in)
                buf_in = ""
        else:
            buf_in += ch
    return lineas

# CIRCUITO SUMADOR +5 
def bin4(v):
    return "{:04b}".format(v & 0xF)

def set_bits(valor):
    """Pone los 4 bits menos significativos de 'valor' en GP2..GP5."""
    a0.value(valor & 1)
    a1.value((valor >> 1) & 1)
    a2.value((valor >> 2) & 1)
    a3.value((valor >> 3) & 1)

def suma5(valor):
    """El mismo +5 que hace el circuito, pero por software (mod 16)."""
    return (valor + 5) & 0xF

def leer_enable():
    """1 = habilitado (switch cerrado, GP6=0).  0 = deshabilitado."""
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
    print("MORSE:" + disp)

def _letra_lista(codigo):
    """Una letra quedo lista: la decodifica y, si es valida, saca su ASCII
    y los 4 bits menos significativos hacia el circuito."""
    global texto
    L = MORSE_INV.get(codigo, "")
    texto += L
    if L:
        ascii_v = ord(L)
        bits4 = ascii_v & 0xF
        set_bits(bits4)                      # esto maneja el circuito fisico
        # Avisa a la compu: letra, ASCII, los 4 bits, el +5 por software, y el switch
        print("INFO:%s;%d;%s;%s;%d" % (L, ascii_v, bin4(bits4), bin4(suma5(bits4)), leer_enable()))

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
    print("TEXTO:" + texto)
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
        print("PONG")
    elif cmd.startswith("FRASE:"):
        frase_actual = cmd[6:]
        print("LISTO")
    elif cmd == "SHOW":
        mostrar_frase(usar_buzzer=False)
        print("LISTO")
    elif cmd == "PLAY":
        mostrar_frase(usar_buzzer=True)
        print("LISTO")
    elif cmd == "LEDS_OFF":
        led_show.off()
        tono_off()
        print("LISTO")
    elif cmd.startswith("UNIDAD:"):
        try:
            unidad = float(cmd[7:])
        except Exception:
            pass
        print("UNIDAD_OK")
    elif cmd == "MODO":
        print("MODO:" + leer_modo())
    elif cmd == "CAPTURAR":
        iniciar_captura()
    #Circuito sumador +5
    elif cmd.startswith("BITS:"):
        # Modo prueba: meter los 4 bits a mano. Ej: BITS:0101
        s = cmd[5:].strip()
        try:
            valor = int(s, 2) & 0xF
        except Exception:
            valor = 0
        set_bits(valor)
        # Responde: entrada, +5 por software, y estado del switch
        print("RES:%s;%s;%d" % (bin4(valor), bin4(suma5(valor)), leer_enable()))
    elif cmd == "ENABLE?":
        print("ENABLE:%d" % leer_enable())

# BUCLE PRINCIPAL 
set_bits(0)
while True:
    for linea in leer_lineas():
        procesar(linea.strip())
    if capturando:
        actualizar_captura()
    time.sleep_ms(2)
