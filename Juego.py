import sys, os, math, time, random, threading
from dataclasses import dataclass, field
from typing import List, Optional
from difflib import SequenceMatcher

import pygame
import socket

ANCHO  = 900
ALTO   = 650
FPS    = 60
TITULO = "StrangerTEC · Morse Translator"

# ============================================================
#   CONEXION CON LA PICO POR WIFI  (en vez de USB serial)
# ============================================================
# Poné aquí la MISMA IP y puerto que configuraste en la Pico.
PICO_IP   = "192.168.43.169"
PICO_PORT = 1234
# ============================================================

TIMEOUT_CONEXION = 4       # segundos para conectar el socket
TIMEOUT_RESP  = 8
UNIDAD_A = 0.2
UNIDAD_B = 0.3

NEGRO     = (  0,   0,   0)
GRIS_OSC  = ( 35,  35,  45)
GRIS      = (120, 120, 130)
BLANCO    = (240, 235, 225)
ROJO_NEON = (255,  40,  40)
NARANJA   = (255, 130,   0)
AMARILLO  = (255, 210,   0)
VERDE_NEON= ( 50, 255, 100)
AZUL_NEON = ( 60, 160, 255)
COLOR_A   = AMARILLO
COLOR_B   = AZUL_NEON

F_MONO  = "Courier New"
F_TITLE = "Impact"
F_BODY  = "Arial"

TOTAL_RONDAS    = 3
FRASES_POR_RONDA = 3
FRASES = [
    "SOS SOS ", "SI", "NO", "HOLA", "PYTHON",
    "MAQUETA", "MORSE", "LUZ", "PICO", "TEC",
]

MORSE = {
    'A':'.-',   'B':'-...', 'C':'-.-.', 'D':'-..',  'E':'.',
    'F':'..-.', 'G':'--.',  'H':'....', 'I':'..',   'J':'.---',
    'K':'-.-',  'L':'.-..', 'M':'--',   'N':'-.',   'O':'---',
    'P':'.--.', 'Q':'--.-', 'R':'.-.',  'S':'...',  'T':'-',
    'U':'..-',  'V':'...-', 'W':'.--',  'X':'-..-', 'Y':'-.--',
    'Z':'--..',
    '0':'-----','1':'.----','2':'..---','3':'...--','4':'....-',
    '5':'.....','6':'-....','7':'--...','8':'---..','9':'----.',
    '+':'.-.-.','-':'-....-',' ':'/'
}
MORSE_INV = {v: k for k, v in MORSE.items()}

def texto_a_morse(texto):
    tokens = []
    for c in texto.upper():
        if c == ' ':
            tokens.append('/')
        elif c in MORSE:
            tokens.append(MORSE[c])
    return ' '.join(tokens)


def morse_a_texto(morse_raw):
    morse_raw = morse_raw.replace('/', '   ').strip()
    resultado = []
    for palabra in morse_raw.split('   '):
        letras = ''
        for codigo in palabra.strip().split(' '):
            codigo = codigo.strip()
            if codigo in MORSE_INV:
                letras += MORSE_INV[codigo]
        if letras:
            resultado.append(letras)
    return ' '.join(resultado)


def calcular_puntaje(original, respuesta):
    orig = original.replace(' ', '').strip().upper()
    resp = respuesta.replace(' ', '').strip().upper()
    if not orig:
        return 0
    return int(SequenceMatcher(None, orig, resp).ratio() * 100)


def color_puntaje(puntaje):
    if puntaje == 100:
        return VERDE_NEON
    elif puntaje >= 60:
        return AMARILLO
    return ROJO_NEON


class CapturadorMorse:
    def __init__(self, unidad=0.2):
        self.unidad        = unidad
        self.morse_final   = ""
        self.letra_actual  = ""
        self.presionado    = False
        self._t_press      = 0.0
        self._t_soltar     = 0.0
        self._letra_ok     = False

    def reset(self):
        self.morse_final  = ""
        self.letra_actual = ""
        self.presionado   = False
        self._t_press     = 0.0
        self._t_soltar    = 0.0
        self._letra_ok    = False

    def presionar(self):
        if not self.presionado:
            self._t_press  = time.time()
            self.presionado = True

    def soltar(self):
        if self.presionado:
            duracion        = time.time() - self._t_press
            self.presionado = False
            self._t_soltar  = time.time()
            self._letra_ok  = False
            self.letra_actual += '.' if duracion < self.unidad * 2 else '-'

    def actualizar(self):
        if self.presionado or self._t_soltar == 0.0:
            return
        silencio = time.time() - self._t_soltar
        if silencio >= self.unidad * 3 and self.letra_actual and not self._letra_ok:
            if self.morse_final and not self.morse_final.endswith('   '):
                self.morse_final += ' '
            self.morse_final  += self.letra_actual
            self.letra_actual  = ""
            self._letra_ok     = True
        if silencio >= self.unidad * 7 and self._letra_ok:
            if not self.morse_final.endswith('   '):
                self.morse_final += '   '

    def confirmar(self):
        if self.letra_actual:
            if self.morse_final and not self.morse_final.endswith('   '):
                self.morse_final += ' '
            self.morse_final  += self.letra_actual
            self.letra_actual  = ""
        return morse_a_texto(self.morse_final)

    @property #hace que se pueda llamar sin parentesis, como un atributo
    def morse_display(self):
        return self.morse_final + self.letra_actual

    @property
    def traduccion(self):
        return morse_a_texto(self.morse_final)


class ConexionPico:
    """Ahora habla con la Pico por WiFi (socket TCP) en vez de USB serial.
    El protocolo de comandos es exactamente el mismo de antes."""
    def __init__(self):
        self.sock          = None
        self.conectada     = False
        self._lock         = threading.Lock()
        self._buffer       = []
        self.morse_live    = ""
        self.texto_pico    = ""
        self.esperando     = False
        self.modo_maqueta  = "local"
        self.ult_info      = None   # (letra, ascii, bits, suma, enable) de la ultima letra de B
        self.enable_estado = None   # "1"/"0" del switch de habilitacion
        self._rx           = ""     # buffer de texto recibido sin procesar

    def conectar(self, ip=None, puerto=None):
        ip     = ip or PICO_IP
        puerto = puerto or PICO_PORT
        # cerrar cualquier socket viejo antes de reintentar
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None
        self.esperando = False
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(TIMEOUT_CONEXION)
            s.connect((ip, puerto))
            s.settimeout(None)
            self.sock      = s
            self.conectada = True
            self._rx       = ""
            with self._lock:
                self._buffer = []
            threading.Thread(target=self._leer, daemon=True).start()
            return True
        except OSError:
            self.sock      = None
            self.conectada = False
            return False

    def desconectar(self):
        self.conectada = False
        self.esperando = False
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
        self.sock = None

    def _leer(self):
        try:
            self.sock.settimeout(0.2)
        except Exception:
            return
        while self.conectada and self.sock:
            try:
                data = self.sock.recv(512)
                if not data:
                    self.conectada = False
                    break
                self._rx += data.decode("utf-8", errors="ignore")
                while "\n" in self._rx:
                    linea, self._rx = self._rx.split("\n", 1)
                    linea = linea.strip()
                    if linea:
                        self._clasificar(linea)
            except socket.timeout:
                continue
            except OSError:
                self.conectada = False
                break
        self.conectada = False
        self.esperando = False
        self.sock = None

    def _clasificar(self, linea):
        with self._lock:
            if linea.startswith("SIMBOLO:"):
                self.morse_live += linea[8:].strip()
            elif linea == "PALABRA":
                if not self.morse_live.endswith("   "):
                    self.morse_live += "   "
            elif linea.startswith("MORSE:"):
                self.morse_live = linea[6:].strip()
            elif linea.startswith("TEXTO:"):
                self.texto_pico = linea[6:].strip()
                self.esperando  = False
            elif linea.startswith("MODO:"):
                self.modo_maqueta = linea[5:].strip()
                self._buffer.append(linea)
            elif linea.startswith("INFO:"):
                p = linea[5:].split(";")
                if len(p) == 5:
                    self.ult_info = (p[0], p[1], p[2], p[3], p[4])
                    self.enable_estado = p[4]
            elif linea.startswith("RES:"):
                self._buffer.append(linea)
            elif linea.startswith("ENABLE:"):
                self.enable_estado = linea[7:].strip()
                self._buffer.append(linea)
            else:
                self._buffer.append(linea)

    def enviar(self, comando):
        if self.sock and self.conectada:
            try:
                self.sock.sendall((comando + "\n").encode())
            except OSError:
                self.conectada = False

    def esperar_respuesta(self, clave, timeout=TIMEOUT_RESP):
        t0 = time.time()
        while time.time() - t0 < timeout:
            with self._lock:
                for i, linea in enumerate(self._buffer):
                    if linea.startswith(clave):
                        return self._buffer.pop(i)
            time.sleep(0.05)
        return None

    def ping(self):
        self.enviar("PING")
        return self.esperar_respuesta("PONG", timeout=2) is not None

    def enviar_frase(self, frase):
        self.enviar(f"FRASE:{frase.upper()}")
        self.esperar_respuesta("LISTO", timeout=3)

    def mostrar_leds(self):
        self.enviar("SHOW")
        self.esperar_respuesta("LISTO", timeout=15)

    def reproducir_sonido(self):
        self.enviar("PLAY")
        self.esperar_respuesta("LISTO", timeout=15)

    def apagar_leds(self):
        self.enviar("LEDS_OFF")
        self.esperar_respuesta("LISTO", timeout=3)

    def configurar_unidad(self, unidad):
        self.enviar(f"UNIDAD:{unidad}")
        self.esperar_respuesta("UNIDAD_OK", timeout=3)

    def iniciar_captura(self):
        with self._lock:
            self.morse_live = ""
            self.texto_pico = ""
            self.ult_info   = None
            self.esperando  = False
        if self.sock and self.conectada:
            self.enviar("CAPTURAR")
            self.esperando = self.conectada

    def enviar_bits(self, bits_str):
        self.enviar("BITS:" + bits_str)
        resp = self.esperar_respuesta("RES:", timeout=2)
        if resp:
            p = resp[4:].split(";")
            if len(p) == 3:
                return p[0], p[1], p[2]
        return None

    def leer_enable(self):
        self.enviar("ENABLE?")
        resp = self.esperar_respuesta("ENABLE:", timeout=2)
        if resp:
            self.enable_estado = resp[7:].strip()
        return self.enable_estado

    def leer_modo(self):
        self.enviar("MODO")
        resp = self.esperar_respuesta("MODO:", timeout=3)
        if resp:
            self.modo_maqueta = resp[5:].strip()
        return self.modo_maqueta


@dataclass
class ResultadoFrase:
    frase_original: str
    respuesta_a: str = ""
    respuesta_b: str = ""
    puntaje_a: int = 0
    puntaje_b: int = 0
    tiempo_a: float = 0.0
    tiempo_b: float = 0.0

    @property
    def ganador(self):
        if self.puntaje_a > self.puntaje_b:
            return 'A'
        elif self.puntaje_b > self.puntaje_a:
            return 'B'
        return None


@dataclass
class ResultadoRonda:
    numero: int
    frases: List[ResultadoFrase] = field(default_factory=list)

    @property
    def puntos_a(self):
        return sum(1 for f in self.frases if f.ganador == 'A')

    @property
    def puntos_b(self):
        return sum(1 for f in self.frases if f.ganador == 'B')

    @property
    def ganador(self):
        if self.puntos_a > self.puntos_b:
            return 'A'
        elif self.puntos_b > self.puntos_a:
            return 'B'
        return None


class GameManager:
    MENU   = "menu"
    FINAL  = "final"

    def __init__(self):
        self.rondas        = []
        self.ronda_actual  = None
        self.frases_usadas = []
        self.frases_ronda  = []
        self.idx_frase     = 0
        self.estado        = self.MENU
        self.unidad_tiempo = UNIDAD_A
        self.con_maqueta   = False
        self.modo_display  = "local"

    def nueva_partida(self):
        self.rondas        = []
        self.frases_usadas = []
        self._iniciar_ronda(1)

    def _iniciar_ronda(self, numero):
        disponibles = [f for f in FRASES if f not in self.frases_usadas]
        if len(disponibles) < FRASES_POR_RONDA:
            disponibles = list(FRASES)
        self.frases_ronda = random.sample(disponibles, FRASES_POR_RONDA)
        self.frases_usadas.extend(self.frases_ronda)
        self.ronda_actual  = ResultadoRonda(numero=numero)
        self.idx_frase     = 0

    @property
    def numero_ronda(self):
        return len(self.rondas) + 1

    @property
    def frase_actual(self):
        if self.idx_frase < len(self.frases_ronda):
            return self.frases_ronda[self.idx_frase]
        return ""

    @property
    def ronda_terminada(self):
        return self.idx_frase >= FRASES_POR_RONDA

    def registrar_resultado(self, resp_a, resp_b, punt_a, punt_b, t_a=0.0, t_b=0.0):
        self.ronda_actual.frases.append(ResultadoFrase(
            frase_original=self.frase_actual,
            respuesta_a=resp_a, respuesta_b=resp_b,
            puntaje_a=punt_a,   puntaje_b=punt_b,
            tiempo_a=t_a,       tiempo_b=t_b,
        ))
        self.idx_frase += 1

    def cerrar_ronda(self):
        self.rondas.append(self.ronda_actual)
        sig = self.numero_ronda
        if sig <= TOTAL_RONDAS:
            self._iniciar_ronda(sig)
        else:
            self.estado = self.FINAL

    @property
    def rondas_ganadas_a(self):
        return sum(1 for r in self.rondas if r.ganador == 'A')

    @property
    def rondas_ganadas_b(self):
        return sum(1 for r in self.rondas if r.ganador == 'B')

    @property
    def ganador_partida(self):
        if self.rondas_ganadas_a > self.rondas_ganadas_b:
            return 'A'
        elif self.rondas_ganadas_b > self.rondas_ganadas_a:
            return 'B'
        return None

    @property
    def ultimo_resultado(self):
        if self.ronda_actual and self.ronda_actual.frases:
            return self.ronda_actual.frases[-1]
        return None


def blit_c(surf, texto, fuente, color, y, alpha=255):
    img = fuente.render(texto, True, color)
    if alpha < 255:
        img.set_alpha(alpha)
    surf.blit(img, ((ANCHO - img.get_width()) // 2, y))


def caja(surf, x, y, w, h, color, radio=6):
    pygame.draw.rect(surf, GRIS_OSC, (x, y, w, h), border_radius=radio)
    pygame.draw.rect(surf, color,    (x, y, w, h), 2, border_radius=radio)


def boton(surf, fuente, texto, x, y, w, h, color):
    caja(surf, x, y, w, h, color)
    img = fuente.render(texto, True, color)
    surf.blit(img, (x + (w - img.get_width()) // 2,
                    y + (h - img.get_height()) // 2))


def header(surf, texto, f_titulo):
    pygame.draw.rect(surf, GRIS_OSC, (0, 0, ANCHO, 56))
    pygame.draw.line(surf, ROJO_NEON, (0, 56), (ANCHO, 56), 1)
    blit_c(surf, texto, f_titulo, ROJO_NEON, 14)


def pantalla_menu(screen, clock):
    f_tit = pygame.font.SysFont(F_BODY, 48, bold=True)
    f_sub = pygame.font.SysFont(F_BODY, 22)
    f_men = pygame.font.SysFont(F_BODY, 26)
    f_sml = pygame.font.SysFont(F_MONO, 17)

    opciones = ["JUGAR", "MODO PRUEBA", "SALIR"]
    sel      = 0

    while True:
        screen.fill((8, 5, 10))

        # Titulo
        blit_c(screen, "StrangerTEC", f_tit, ROJO_NEON, 180)
        blit_c(screen, "Morse Translator", f_sub, (160, 30, 30), 240)

        # Opciones del menu
        for i, op in enumerate(opciones):
            activo = (i == sel)
            color  = AMARILLO if activo else GRIS
            txt    = ("> " if activo else "  ") + op
            img    = f_men.render(txt, True, color)
            x      = (ANCHO - img.get_width()) // 2
            y      = 340 + i * 55
            if activo:
                pygame.draw.rect(screen, GRIS_OSC, (x - 15, y - 5, img.get_width() + 30, 38))
            screen.blit(img, (x, y))

        blit_c(screen, "Flechas arriba/abajo para mover   ENTER para confirmar", f_sml, GRIS, 480)
        pygame.display.flip()

        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                return 'salir'
            if ev.type == pygame.KEYDOWN:
                if ev.key in (pygame.K_UP, pygame.K_w):
                    sel = (sel - 1) % len(opciones)
                elif ev.key in (pygame.K_DOWN, pygame.K_s):
                    sel = (sel + 1) % len(opciones)
                elif ev.key == pygame.K_RETURN:
                    return ['jugar', 'prueba', 'salir'][sel]
                elif ev.key == pygame.K_ESCAPE:
                    return 'salir'
        clock.tick(FPS)



def pantalla_config(screen, clock, pico):
    f_tit = pygame.font.SysFont(F_BODY, 30, bold=True)
    f_med = pygame.font.SysFont(F_BODY, 24)
    f_sml = pygame.font.SysFont(F_MONO, 18)

    conectada    = pico.conectar()
    unidad       = UNIDAD_A
    modo_display = "local"
    mensaje      = ""
    t_msg        = 0.0

    if conectada:
        pico.ping()
        modo_display = pico.leer_modo()
        pico.configurar_unidad(unidad)
        mensaje = "✓ Maqueta conectada"
        t_msg   = time.time()
    else:
        mensaje = "✗ Maqueta no encontrada"
        t_msg   = time.time()

    while True:
        screen.fill((8, 5, 10))
        header(screen, "CONFIGURACIÓN", f_tit)

        # Estado maqueta
        img = f_med.render("Maqueta (Raspberry Pi Pico 2W)", True, BLANCO)
        screen.blit(img, (60, 70))
        col_est = VERDE_NEON if conectada else ROJO_NEON
        img2 = f_med.render("CONECTADA" if conectada else "NO CONECTADA", True, col_est)
        screen.blit(img2, (ANCHO - img2.get_width() - 60, 70))

        boton(screen, f_sml, "Reintentar (R)", 60,  108, 220, 36, NARANJA if not conectada else GRIS)
        boton(screen, f_sml, "Sin maqueta (S)",300, 108, 220, 36, AZUL_NEON)

        # Velocidad
        pygame.draw.line(screen, GRIS_OSC, (40, 158), (ANCHO-40, 158), 1)
        screen.blit(f_med.render("Velocidad Morse", True, BLANCO), (60, 165))
        boton(screen, f_sml, "Unidad A  0.2s  [1]", 60,  198, 230, 42, AMARILLO if unidad == UNIDAD_A else GRIS)
        boton(screen, f_sml, "Unidad B  0.3s  [2]", 310, 198, 230, 42, AMARILLO if unidad == UNIDAD_B else GRIS)
        screen.blit(f_sml.render("Unidad A = rápido   Unidad B = lento", True, GRIS), (60, 248))

        # Modo dipswitch
        pygame.draw.line(screen, GRIS_OSC, (40, 272), (ANCHO-40, 272), 1)
        screen.blit(f_med.render("Modo detectado en dipswitch:", True, BLANCO), (60, 280))
        if conectada:
            modo_txt = "LUCES (Jugador B ve los LEDs)" if modo_display == "local" else "SONIDO (Jugador B escucha el buzzer)"
            screen.blit(f_sml.render(modo_txt, True, NARANJA if modo_display == "local" else AZUL_NEON), (60, 308))
        else:
            screen.blit(f_sml.render("(conecta la maqueta para detectar modo)", True, GRIS), (60, 308))

        # Reglas
        pygame.draw.line(screen, GRIS_OSC, (40, 332), (ANCHO-40, 332), 1)
        reglas = [
            ("Reglas:", True),
            ("  · 3 rondas · 3 frases por ronda · ambos juegan al mismo tiempo", False),
            ("  · Jugador A: ESPACIO en teclado.  Jugador B: botón de la maqueta.", False),
            ("  · Gana la frase quien tenga mayor puntaje de similitud.", False),
            ("  · Gana la ronda quien gane más frases.", False),
            ("  · Gana la partida quien gane más rondas.", False),
        ]
        for i, (txt, bold) in enumerate(reglas):
            f_r = pygame.font.SysFont(F_MONO, 17, bold=bold)
            screen.blit(f_r.render(txt, True, AMARILLO if bold else GRIS), (60, 342 + i * 22))

        # Mensaje temporal
        if mensaje:
            alpha = max(0, int(255 * (1 - (time.time() - t_msg) / 3)))
            if alpha > 0:
                img_m = f_sml.render(mensaje, True, VERDE_NEON if conectada else ROJO_NEON)
                img_m.set_alpha(alpha)
                screen.blit(img_m, (60, 478))

        # Botón comenzar
        pygame.draw.line(screen, GRIS_OSC, (40, 496), (ANCHO-40, 496), 1)
        boton(screen, f_med, "▶  COMENZAR  (ENTER)", ANCHO//2 - 180, 506, 360, 46, VERDE_NEON)
        img_e = f_sml.render("ESC = Volver al menú", True, GRIS)
        screen.blit(img_e, (ANCHO - img_e.get_width() - 30, 518))

        pygame.display.flip()

        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                return None
            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    return None
                elif ev.key == pygame.K_RETURN:
                    return {'con_maqueta': conectada, 'unidad': unidad, 'modo_display': modo_display}
                elif ev.key == pygame.K_1:
                    unidad = UNIDAD_A
                    if conectada: pico.configurar_unidad(unidad)
                elif ev.key == pygame.K_2:
                    unidad = UNIDAD_B
                    if conectada: pico.configurar_unidad(unidad)
                elif ev.key == pygame.K_r and not conectada:
                    conectada = pico.conectar()
                    if conectada:
                        pico.ping()
                        modo_display = pico.leer_modo()
                        pico.configurar_unidad(unidad)
                        mensaje = "✓ Maqueta conectada"
                    else:
                        mensaje = "✗ No se encontró la maqueta"
                    t_msg = time.time()
                elif ev.key == pygame.K_s:
                    return {'con_maqueta': False, 'unidad': unidad, 'modo_display': 'local'}
        clock.tick(FPS)


def pantalla_partida(screen, clock, frase, pico, con_maqueta, unidad, modo_display):
    f_tit  = pygame.font.SysFont(F_BODY, 26, bold=True)
    f_gran = pygame.font.SysFont(F_BODY, 34, bold=True)
    f_med  = pygame.font.SysFont(F_BODY, 22)
    f_sml  = pygame.font.SysFont(F_MONO, 17)
    f_mor  = pygame.font.SysFont(F_MONO, 19)

    mostrado  = [False]

    #Fase 1: mostrar la frase en la maqueta 
    def _enviar():
        if con_maqueta:
            pico.enviar_frase(frase)
            if modo_display == "local":
                pico.mostrar_leds()
            else:
                pico.reproducir_sonido()
        mostrado[0] = True

    threading.Thread(target=_enviar, daemon=True).start()
    t0 = time.time()

    while True:
        screen.fill((8, 5, 10))
        header(screen, "OBSERVA LA FRASE", f_tit)
        caja(screen, 40, 68, ANCHO-80, 76, AMARILLO)
        blit_c(screen, frase, f_gran, BLANCO, 80)
        if con_maqueta:
            est = ("Enviando..." if not mostrado[0] else "✓ Maqueta lista")
            blit_c(screen, est, f_sml, NARANJA if not mostrado[0] else VERDE_NEON, 210)
        else:
            blit_c(screen, "(sin maqueta — memoriza la frase)", f_sml, GRIS, 210)
        blit_c(screen, "ENTER = Comenzar  |  ESC = Salir", f_sml, AMARILLO, 270)
        et = time.time() - t0
        screen.blit(f_sml.render(f"t={et:.1f}s", True, GRIS_OSC), (ANCHO-70, ALTO-24))
        pygame.display.flip()

        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                return None
            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_RETURN and mostrado[0]:
                    break
                if ev.key == pygame.K_ESCAPE:
                    return None
        else:
            clock.tick(FPS)
            continue
        break

    #Fase 2: ambos ingresan Morse simultáneamente 
    cap_a        = CapturadorMorse(unidad=unidad)
    t0           = time.time()
    resp_a       = [None]
    t_a          = [0.0]
    confirmado_a = False

    if con_maqueta:
        pico.iniciar_captura()

    while True:
        elapsed      = time.time() - t0
        pico_termino = con_maqueta and (not pico.esperando or not pico.conectada)

        if confirmado_a and (not con_maqueta or pico_termino):
            break

        screen.fill((8, 5, 10))
        header(screen, f"INGRESA EN MORSE  —  {elapsed:.1f}s", f_tit)
        caja(screen, 40, 62, ANCHO-80, 50, BLANCO)
        blit_c(screen, frase, pygame.font.SysFont(F_BODY, 28, bold=True), BLANCO, 72)

        col_w = (ANCHO - 70) // 2

        # Panel A
        caja(screen, 30, 124, col_w, 28, COLOR_A)
        screen.blit(f_med.render("JUGADOR A  (teclado)", True, COLOR_A), (38, 129))
        caja(screen, 30, 157, col_w, 42, COLOR_A)
        screen.blit(f_mor.render(cap_a.morse_display[-36:], True, BLANCO), (38, 168))
        caja(screen, 30, 204, col_w, 34, COLOR_A)
        screen.blit(f_sml.render(cap_a.traduccion[:28], True, VERDE_NEON), (38, 213))
        if confirmado_a:
            st = f_sml.render("✓ CONFIRMADO", True, VERDE_NEON)
        elif cap_a.presionado:
            st = f_sml.render("● presionando...", True, AMARILLO)
        else:
            st = f_sml.render("ESPACIO=Morse  |  ENTER=Confirmar", True, GRIS)
        screen.blit(st, (38, 244))

        # Panel B
        bx = 30 + col_w + 10
        caja(screen, bx, 124, col_w, 28, COLOR_B)
        screen.blit(f_med.render("JUGADOR B  (maqueta)" if con_maqueta else "JUGADOR B  (sin maqueta)", True, COLOR_B), (bx+8, 129))
        if con_maqueta:
            with pico._lock:
                mlive = pico.morse_live[-36:]
            caja(screen, bx, 157, col_w, 42, COLOR_B)
            screen.blit(f_mor.render(mlive, True, BLANCO), (bx+8, 168))
            caja(screen, bx, 204, col_w, 34, COLOR_B)
            screen.blit(f_sml.render(morse_a_texto(pico.morse_live)[:28], True, VERDE_NEON), (bx+8, 213))
            if not pico.conectada:
                stb = f_sml.render("¡Maqueta desconectada!  Presiona ESC", True, ROJO_NEON)
            elif pico_termino:
                stb = f_sml.render("✓ CAPTURA TERMINADA", True, VERDE_NEON)
            else:
                stb = f_sml.render("Esperando botón... (3s silencio = fin)", True, NARANJA)
            screen.blit(stb, (bx+8, 244))
        else:
            caja(screen, bx, 157, col_w, 121, GRIS_OSC)
            screen.blit(f_sml.render("Maqueta no conectada.", True, GRIS), (bx+8, 212))


        # --- Reflejo del circuito +5 (ultima letra del Jugador B) ---
        if con_maqueta:
            caja(screen, 30, 286, ANCHO - 60, 74, VERDE_NEON)
            screen.blit(f_sml.render("CIRCUITO +5  ·  ultima letra del Jugador B", True, VERDE_NEON), (40, 292))
            info = pico.ult_info
            if info:
                L, asc, b4, suma, en = info
                l1 = "Letra '{}'    ASCII {}    bits {}    +5 = {}".format(L, asc, b4, suma)
                screen.blit(f_mor.render(l1, True, BLANCO), (40, 316))
                if en == "1":
                    screen.blit(f_sml.render("Switch ON  ->  los 4 LEDs muestran  {}".format(suma), True, AMARILLO), (40, 339))
                else:
                    screen.blit(f_sml.render("Switch OFF  ->  LEDs apagados (enciende el switch)", True, NARANJA), (40, 339))
            else:
                screen.blit(f_sml.render("(escribe una letra con el boton para ver su ASCII y su +5)", True, GRIS), (40, 320))

        guia = f_sml.render("ESPACIO corto=.  ESPACIO largo=-  ENTER=Confirmar  ESC=Salir", True, GRIS)
        screen.blit(guia, ((ANCHO - guia.get_width())//2, ALTO-26))

        cap_a.actualizar()
        pygame.display.flip()

        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                return None
            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_SPACE and not confirmado_a:
                    cap_a.presionar()
                elif ev.key == pygame.K_RETURN and not confirmado_a:
                    resp_a[0]    = cap_a.confirmar()
                    t_a[0]       = time.time() - t0
                    confirmado_a = True
                    if not con_maqueta:
                        break
                elif ev.key == pygame.K_ESCAPE:
                    return None
            if ev.type == pygame.KEYUP:
                if ev.key == pygame.K_SPACE and not confirmado_a:
                    cap_a.soltar()
        clock.tick(FPS)

    ra = resp_a[0] or cap_a.confirmar()
    ta = t_a[0]
    if con_maqueta:
        with pico._lock:
            rb = pico.texto_pico.strip() if (pico.conectada and not pico.esperando) else ""
        if not pico.conectada:
            rb = ""
        tb = time.time() - t0
    else:
        rb, tb = "", 0.0

    return ra, rb, ta, tb

def pantalla_resultado_frase(screen, clock, rf, num_frase, puntos_a, puntos_b):
    f_tit  = pygame.font.SysFont(F_BODY, 26, bold=True)
    f_gran = pygame.font.SysFont(F_BODY, 36, bold=True)
    f_med  = pygame.font.SysFont(F_BODY, 22)
    f_sml  = pygame.font.SysFont(F_MONO, 17)
    t0     = time.time()

    while True:
        t = time.time() - t0
        screen.fill((8, 5, 10))
        header(screen, f"RESULTADO — FRASE {num_frase}", f_tit)

        blit_c(screen, "Frase original:", f_sml, GRIS, 66)
        caja(screen, 40, 80, ANCHO-80, 52, BLANCO)
        blit_c(screen, rf.frase_original, f_gran, BLANCO, 88)

        col_w = (ANCHO - 80) // 2
        for idx, (jug, resp, punt, col) in enumerate([
            ("JUGADOR A", rf.respuesta_a, rf.puntaje_a, COLOR_A),
            ("JUGADOR B", rf.respuesta_b, rf.puntaje_b, COLOR_B),
        ]):
            cx = 30 + idx * (col_w + 20)
            caja(screen, cx, 146, col_w, 26, col)
            screen.blit(f_med.render(jug, True, col), (cx+8, 151))
            caja(screen, cx, 177, col_w, 34, col)
            screen.blit(f_sml.render(f"Escribió: {(resp or '(vacío)')[:20]}", True, BLANCO), (cx+8, 187))
            caja(screen, cx, 216, col_w, 34, col)
            screen.blit(f_med.render(f"Puntaje: {punt}%", True, color_puntaje(punt)), (cx+8, 224))

        # Ganador
        if rf.ganador == 'A':
            gtxt, gcol = "▲  JUGADOR A GANA ESTA FRASE", COLOR_A
        elif rf.ganador == 'B':
            gtxt, gcol = "▲  JUGADOR B GANA ESTA FRASE", COLOR_B
        else:
            gtxt, gcol = "EMPATE — nadie suma punto", BLANCO

        blit_c(screen, gtxt, f_med, gcol, 266, int(180 + 75 * abs(math.sin(t * 3))))

        pygame.draw.line(screen, GRIS_OSC, (40, 302), (ANCHO-40, 302), 1)
        blit_c(screen, "Marcador de ronda:", f_sml, GRIS, 312)
        blit_c(screen, f"Jugador A: {puntos_a}   —   Jugador B: {puntos_b}", f_med, BLANCO, 334)
        blit_c(screen, "ENTER = siguiente frase   |   ESC = salir", f_sml, GRIS, ALTO-34)

        pygame.display.flip()
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                return False
            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_RETURN:
                    return True
                if ev.key == pygame.K_ESCAPE:
                    return False
        clock.tick(30)

def pantalla_resultado_ronda(screen, clock, ronda, rga, rgb):
    f_tit  = pygame.font.SysFont(F_BODY, 26, bold=True)
    f_gran = pygame.font.SysFont(F_BODY, 38, bold=True)
    f_med  = pygame.font.SysFont(F_BODY, 22)
    f_sml  = pygame.font.SysFont(F_MONO, 17)
    t0     = time.time()

    while True:
        t = time.time() - t0
        screen.fill((8, 5, 10))
        header(screen, f"FIN DE RONDA {ronda.numero}", f_tit)

        blit_c(screen, "Resultados de frases:", f_sml, GRIS, 68)
        for i, rf in enumerate(ronda.frases):
            if rf.ganador == 'A':
                gt, gc = "→ A", COLOR_A
            elif rf.ganador == 'B':
                gt, gc = "→ B", COLOR_B
            else:
                gt, gc = "→ Empate", GRIS
            lin = f_sml.render(f'  Frase {i+1}: "{rf.frase_original}"   A={rf.puntaje_a}%  B={rf.puntaje_b}%   {gt}', True, gc)
            screen.blit(lin, ((ANCHO - lin.get_width())//2, 90 + i*24))

        y1 = 90 + len(ronda.frases)*24 + 16
        pygame.draw.line(screen, GRIS_OSC, (40, y1), (ANCHO-40, y1), 1)
        pts = f_med.render(f"Jugador A: {ronda.puntos_a} frase(s)    Jugador B: {ronda.puntos_b} frase(s)", True, BLANCO)
        screen.blit(pts, ((ANCHO - pts.get_width())//2, y1+12))

        if ronda.ganador == 'A':
            gt, gc = "JUGADOR A GANA LA RONDA", COLOR_A
        elif ronda.ganador == 'B':
            gt, gc = "JUGADOR B GANA LA RONDA", COLOR_B
        else:
            gt, gc = "RONDA EMPATADA", BLANCO

        blit_c(screen, gt, f_gran, gc, y1+46, int(180 + 75 * abs(math.sin(t * 2.5))))

        y2 = y1 + 122
        pygame.draw.line(screen, GRIS_OSC, (40, y2), (ANCHO-40, y2), 1)
        blit_c(screen, "Rondas ganadas:", f_sml, GRIS, y2+8)
        blit_c(screen, f"Jugador A: {rga}   —   Jugador B: {rgb}", f_med, BLANCO, y2+30)
        blit_c(screen, "ENTER = siguiente ronda   |   ESC = salir", f_sml, GRIS, ALTO-34)

        pygame.display.flip()
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                return False
            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_RETURN:
                    return True
                if ev.key == pygame.K_ESCAPE:
                    return False
        clock.tick(30)


def pantalla_final(screen, clock, gm):
    f_tit  = pygame.font.SysFont(F_BODY,  28, bold=True)
    f_gran = pygame.font.SysFont(F_BODY,  42, bold=True)
    f_med  = pygame.font.SysFont(F_BODY,  22)
    f_sml  = pygame.font.SysFont(F_MONO,  17)
    t0     = time.time()
    resumen = [(r.numero, r.puntos_a, r.puntos_b, r.ganador) for r in gm.rondas]

    while True:
        t = time.time() - t0
        screen.fill((8, 5, 10))

        header(screen, "PARTIDA FINALIZADA", f_tit)

        if gm.ganador_partida == 'A':
            gt, gc = "JUGADOR A GANA", COLOR_A
        elif gm.ganador_partida == 'B':
            gt, gc = "JUGADOR B GANA", COLOR_B
        else:
            gt, gc = "EMPATE", BLANCO

        blit_c(screen, gt, f_gran, gc, 66, int(190 + 65 * abs(math.sin(t * 2.8))))

        marc = f_med.render(f"Rondas  —  A: {gm.rondas_ganadas_a}    B: {gm.rondas_ganadas_b}", True, BLANCO)
        screen.blit(marc, ((ANCHO - marc.get_width())//2, 138))

        pygame.draw.line(screen, GRIS_OSC, (40, 172), (ANCHO-40, 172), 1)
        blit_c(screen, "Detalle por ronda:", f_sml, GRIS, 180)
        for i, (num, pa, pb, gan) in enumerate(resumen):
            gt2, gc2 = ("→ A", COLOR_A) if gan == 'A' else (("→ B", COLOR_B) if gan == 'B' else ("→ Empate", GRIS))
            lin = f_sml.render(f"  Ronda {num}:  A ganó {pa} frase(s)   B ganó {pb} frase(s)   {gt2}", True, gc2)
            screen.blit(lin, ((ANCHO - lin.get_width())//2, 204 + i*26))

        fa = sum(1 for r in gm.rondas for f in r.frases if f.ganador == 'A')
        fb = sum(1 for r in gm.rondas for f in r.frases if f.ganador == 'B')
        pygame.draw.line(screen, GRIS_OSC, (40, 294), (ANCHO-40, 294), 1)
        blit_c(screen, f"Frases ganadas totales  —  A: {fa}   B: {fb}", f_sml, GRIS, 304)

        pygame.draw.line(screen, GRIS_OSC, (40, ALTO-100), (ANCHO-40, ALTO-100), 1)
        screen.blit(f_med.render("ENTER = Volver al menú", True, VERDE_NEON),
                    ((ANCHO - f_med.size("ENTER = Volver al menú")[0])//2, ALTO-88))
        screen.blit(f_med.render("ESC   = Salir del juego", True, GRIS),
                    ((ANCHO - f_med.size("ESC   = Salir del juego")[0])//2, ALTO-54))

        pygame.display.flip()
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                return 'salir'
            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_RETURN:
                    return 'menu'
                if ev.key == pygame.K_ESCAPE:
                    return 'salir'
        clock.tick(30)



def pantalla_prueba(screen, clock, pico):
    """Modo prueba del circuito sumador +5.
    Se ponen los 4 bits de entrada a mano (teclas 1-4), se mandan a la maqueta,
    y se compara el resultado en pantalla (software) con los 4 LEDs (fisico)."""
    f_tit = pygame.font.SysFont(F_BODY, 26, bold=True)
    f_med = pygame.font.SysFont(F_BODY, 23)
    f_sml = pygame.font.SysFont(F_MONO, 18)
    f_big = pygame.font.SysFont(F_MONO, 40, bold=True)
    f_lbl = pygame.font.SysFont(F_BODY, 18)

    if not pico.conectada:
        pico.conectar()

    bits = [0, 0, 0, 0]   # indice 0=A0, 1=A1, 2=A2, 3=A3

    def bits_str():
        return "{}{}{}{}".format(bits[3], bits[2], bits[1], bits[0])   # A3..A0

    def valor():
        return bits[0] + bits[1]*2 + bits[2]*4 + bits[3]*8

    def enviar():
        return pico.enviar_bits(bits_str()) if pico.conectada else None

    res = enviar()

    while True:
        screen.fill((8, 5, 10))
        header(screen, "MODO PRUEBA  -  CIRCUITO +5", f_tit)

        conectada = pico.conectada
        if conectada and res is None:
            res = enviar()
        est_txt = "Maqueta conectada" if conectada else "Sin maqueta (solo software)"
        screen.blit(f_sml.render(est_txt, True, VERDE_NEON if conectada else NARANJA), (40, 70))

        # --- Bits de entrada ---
        screen.blit(f_med.render("Entrada (4 bits):", True, BLANCO), (40, 104))
        orden = [3, 2, 1, 0]
        etiquetas = ["A3", "A2", "A1", "A0"]
        bx, by, bw, bh = 250, 142, 86, 86
        for col, idx in enumerate(orden):
            x = bx + col * (bw + 22)
            on = bits[idx] == 1
            color = AMARILLO if on else GRIS
            caja(screen, x, by, bw, bh, color)
            img = f_big.render(str(bits[idx]), True, color)
            screen.blit(img, (x + (bw - img.get_width())//2, by + (bh - img.get_height())//2))
            lab = f_lbl.render(etiquetas[col], True, BLANCO)
            screen.blit(lab, (x + (bw - lab.get_width())//2, by - 26))
            tec = f_sml.render("[{}]".format(col + 1), True, GRIS)
            screen.blit(tec, (x + (bw - tec.get_width())//2, by + bh + 6))

        val = valor()
        suma_sw = (val + 5) % 16
        suma_bin = format(suma_sw, "04b")
        screen.blit(f_med.render("Valor entrada:  {}  =  {} (decimal)".format(bits_str(), val), True, BLANCO), (40, 262))

        pygame.draw.line(screen, GRIS_OSC, (40, 304), (ANCHO-40, 304), 1)

        # --- Resultado por software ---
        screen.blit(f_med.render("Resultado +5 (software):", True, VERDE_NEON), (40, 318))
        screen.blit(f_big.render("{}  =  {}".format(suma_bin, suma_sw), True, VERDE_NEON), (60, 352))

        # --- Lo que dice la maqueta ---
        if conectada and res:
            r_bits, r_suma, r_en = res
            en_on = (r_en == "1")
            screen.blit(f_med.render("Maqueta confirma bits:  {}".format(r_bits), True, BLANCO), (40, 424))
            if en_on:
                screen.blit(f_med.render("Circuito ACTIVADO (switch ON)", True, VERDE_NEON), (40, 456))
                screen.blit(f_sml.render("Mira los 4 LEDs: deben mostrar  {}".format(suma_bin), True, AMARILLO), (40, 492))
            else:
                screen.blit(f_med.render("Circuito DESACTIVADO (switch OFF)", True, NARANJA), (40, 456))
                screen.blit(f_sml.render("Los LEDs estan apagados. Enciende el switch para verlos.", True, NARANJA), (40, 492))
        elif conectada:
            screen.blit(f_med.render("Esperando respuesta de la maqueta...", True, GRIS), (40, 424))
        else:
            screen.blit(f_sml.render("Conecta la maqueta (tecla R) para comparar con los LEDs.", True, GRIS), (40, 424))

        pygame.draw.line(screen, GRIS_OSC, (40, ALTO-66), (ANCHO-40, ALTO-66), 1)
        screen.blit(f_sml.render("Teclas 1-4 = cambiar bits    R = reconectar    ESC = volver al menu", True, GRIS), (40, ALTO-52))

        pygame.display.flip()

        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pico.desconectar(); pygame.quit(); sys.exit(0)
            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    return
                elif ev.key == pygame.K_1:
                    bits[0] ^= 1; res = enviar()
                elif ev.key == pygame.K_2:
                    bits[1] ^= 1; res = enviar()
                elif ev.key == pygame.K_3:
                    bits[2] ^= 1; res = enviar()
                elif ev.key == pygame.K_4:
                    bits[3] ^= 1; res = enviar()
                elif ev.key == pygame.K_r:
                    if not pico.conectada:
                        pico.conectar()
                    res = enviar()
        clock.tick(FPS)


def main():
    pygame.init()
    pygame.display.set_caption(TITULO)
    screen = pygame.display.set_mode((ANCHO, ALTO))
    clock  = pygame.time.Clock()
    pico   = ConexionPico()
    gm     = GameManager()

    corriendo = True
    while corriendo:

        # MENÚ
        opcion = pantalla_menu(screen, clock)
        if opcion == 'salir':
            break
        if opcion == 'prueba':
            pantalla_prueba(screen, clock, pico)
            continue

        # CONFIG
        cfg = pantalla_config(screen, clock, pico)
        if cfg is None:
            continue

        gm.con_maqueta   = cfg['con_maqueta']
        gm.unidad_tiempo = cfg['unidad']
        gm.modo_display  = cfg['modo_display']
        gm.nueva_partida()
        partida_ok = True

        while partida_ok:

            # JUGAR
            res = pantalla_partida(
                screen, clock,
                frase        = gm.frase_actual,
                pico         = pico,
                con_maqueta  = gm.con_maqueta,
                unidad       = gm.unidad_tiempo,
                modo_display = gm.modo_display,
            )
            if res is None:
                partida_ok = False
                break

            ra, rb, ta, tb = res
            pa = calcular_puntaje(gm.frase_actual, ra)
            pb = calcular_puntaje(gm.frase_actual, rb)
            gm.registrar_resultado(ra, rb, pa, pb, ta, tb)

            # RESULTADO FRASE
            rf = gm.ultimo_resultado
            if not pantalla_resultado_frase(
                screen, clock, rf,
                num_frase = gm.ronda_actual.frases.index(rf) + 1,
                puntos_a  = gm.ronda_actual.puntos_a,
                puntos_b  = gm.ronda_actual.puntos_b,
            ):
                partida_ok = False
                break

            # RESULTADO RONDA
            if gm.ronda_terminada:
                if not pantalla_resultado_ronda(
                    screen, clock,
                    ronda = gm.ronda_actual,
                    rga   = gm.rondas_ganadas_a,
                    rgb   = gm.rondas_ganadas_b,
                ):
                    partida_ok = False
                    break

                gm.cerrar_ronda()

                # FINAL
                if gm.estado == GameManager.FINAL:
                    if pantalla_final(screen, clock, gm) == 'salir':
                        corriendo = False
                    partida_ok = False

        if gm.con_maqueta and pico.conectada:
            pico.apagar_leds()

    pico.desconectar()
    pygame.quit()
    sys.exit(0)


if __name__ == "__main__":
    main()