#!/usr/bin/env python3
import sys
import time
import os
import serial
import serial.tools.list_ports

VID_MINITELA = 0x0324 # IDs obtidos via udevadm
PID_MINITELA = 0x0324

# Padrão da maioria dos notebooks. Caso o seu use BAT1, altere aqui.
BAT_NAME = "BAT0"

def encontrar_porta_minitela():
    for porta in serial.tools.list_ports.comports():
        if porta.vid == VID_MINITELA and porta.pid == PID_MINITELA:
            return porta.device
    return None

def normalizar_brilho(valor_brilho):
    try:
        b = int(valor_brilho)
        return max(0, min(100, b)) # Garante limite estrito de 0 a 100
    except (ValueError, TypeError):
        return 50

def ler_sysfs(path):
    """Auxiliar para ler valores inteiros do subsistema /sys de forma segura."""
    try:
        if os.path.exists(path):
            with open(path, "r") as f:
                return int(f.read().strip())
    except (ValueError, IOError):
        pass
    return 0

def obter_dados_energia():
    """Coleta a porcentagem da bateria e calcula o consumo atual em Watts

    fazendo a média de 10 medidas espaçadas por 100ms entre si.
    """
    base_path = f"/sys/class/power_supply/{BAT_NAME}"
    
    if not os.path.exists(base_path):
        return "Bateria: N/A\nConsumo: N/A"
    
    # 1. Obtém a capacidade da bateria (0 a 100)
    capacidade = ler_sysfs(f"{base_path}/capacity")
    
    # 2. Coleta de 10 amostras com intervalo de 100ms
    leituras_watts = []
    for i in range(10):
        power_uw = abs(ler_sysfs(f"{base_path}/power_now"))
        voltage_uv = abs(ler_sysfs(f"{base_path}/voltage_now"))
        current_ua = abs(ler_sysfs(f"{base_path}/current_now"))
        
        if power_uw > 0:
            watts = power_uw / 1_000_000.0
        elif voltage_uv > 0 and current_ua > 0:
            watts = (voltage_uv / 1_000_000.0) * (current_ua / 1_000_000.0)
        else:
            watts = 0.0
            
        leituras_watts.append(watts)
        
        # Só dorme se não for a última iteração (evita delay bobo no final do loop)
        if i < 9:
            time.sleep(0.100)
            
    # Média aritmética exata das 10 medições
    watts_medio = sum(leituras_watts) / len(leituras_watts)
        
    return f"Bateria: {capacidade}%\nConsumo: {watts_medio:.2f}W"

def validar_e_truncar_texto(texto):
    if not texto.isascii():
        print("[-] Erro: O texto contém caracteres não-ASCII.", file=sys.stderr)
        sys.exit(1)
    return texto[:100] if texto else " " # Se string for totalmente vazia, vira espaço.

def main():
    # Agora exige apenas 1 argumento (o brilho). O texto vira opcional.
    if len(sys.argv) < 2:
        print("Uso: sudo ./test_telas.py <porcentagem_brilho> [\"<seu texto>\"]")
        sys.exit(1)

    caminho_dispositivo = encontrar_porta_minitela()
    if not caminho_dispositivo:
        print("[-] Erro: Minitela não encontrada!", file=sys.stderr)
        sys.exit(1)

    brilho_alvo = normalizar_brilho(sys.argv[1])
    
    # Se o usuário passou o texto, usa ele. Se não, gera os dados reais de energia.
    if len(sys.argv) >= 3:
        texto_bruto = sys.argv[2]
    else:
        texto_bruto = obter_dados_energia()
        
    texto_valido = validar_e_truncar_texto(texto_bruto)

    print(f"[*] Minitela: {caminho_dispositivo} | Brilho: {brilho_alvo}%")
    print(f"[*] Texto enviado:\n{texto_valido}")

    try:
        # exclusive=True impede colisões se o script rodar em instâncias simultâneas no Linux
        with serial.Serial(caminho_dispositivo, 115200, timeout=1, write_timeout=1, rtscts=False, dsrdtr=False, exclusive=True) as ser:
            # Configura os estados lógicos imediatamente após a abertura do descritor de arquivo
            ser.dtr = True
            ser.rts = True
            time.sleep(0.1) # Intervalo seguro para o driver CDC do kernel Linux e o firmware da tela estabilizarem a conexão
            
            print("[+] Passo 1/4: Mudando para a tela 2...")
            cmd_tela2 = bytes([0x41, 0x48, 0x80, 0x09, 0x00, 0x90, 0x80, 0x00, 0x02, 0x00, 0x00, 0x00, 0x02, 0xE2, 0xCB, 0x4D, 0x49])
            ser.write(cmd_tela2)
            ser.flush()
            time.sleep(0.1) # Delay essencial de barramento

            print("[+] Passo 2/4: Preparando buffer de texto da tela...")
            cmd_prepara_texto = bytes([0x41, 0x48, 0x00, 0x08, 0x00, 0x90, 0xD0, 0x04, 0x42, 0x00, 0x01, 0x20, 0xE2, 0xCB, 0x4D, 0x49]) # Limpa buffer com espaço
            ser.write(cmd_prepara_texto)
            ser.flush()
            time.sleep(0.1)

            print("[+] Passo 3/4: Renderizando texto real...")
            texto_bytes = texto_valido.encode('ascii')
            tamanho_dados_uteis = 7 + len(texto_bytes) # 7 bytes de cabeçalho interno + payload
            
            # Decompõe explicitamente o tamanho em MSB e LSB, blindando o script contra overflows de buffer
            len_msb = (tamanho_dados_uteis >> 8) & 0xFF
            len_lsb = tamanho_dados_uteis & 0xFF
            
            cmd_texto = bytearray([0x41, 0x48, len_msb, len_lsb, 0x00, 0x90, 0xD0, 0x04, 0x42, 0x00, len(texto_bytes) & 0xFF])
            cmd_texto.extend(texto_bytes)
            cmd_texto.extend([0xE2, 0xCB, 0x4D, 0x49]) # Assinatura mágica de rodapé
            ser.write(bytes(cmd_texto))
            ser.flush()
            time.sleep(0.1)

            print(f"[+] Passo 4/4: Ajustando o brilho para {brilho_alvo}%...")
            cmd_brilho = bytes([0x41, 0x48, 0x80, 0x09, 0x00, 0x90, 0x80, 0x00, 0x07, 0x00, 0x00, 0x00, brilho_alvo, 0xE2, 0xCB, 0x4D, 0x49])
            ser.write(cmd_brilho)
            ser.flush()

            print("[✓] Sucesso! Protocolo concluído de forma idêntica ao app oficial.")

    except serial.SerialException as se:
        print(f"[-] Erro de comunicação serial: {se}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"[-] Erro inesperado no script: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    main()
