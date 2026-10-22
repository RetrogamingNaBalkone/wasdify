; WASDify, MIT. Streaming reflected CRC-32 for the KRS container.
; Input: EAX=raw running state (initial FFFFFFFFh), DS:SI=bytes, CX=count.
; Output: EAX=raw running state (final caller XOR FFFFFFFFh), CF=0.
; CF=1 on count>4096 or segment-boundary overflow; EAX is then unchanged.
; EBX, ECX, EDX, ESI and incoming flags except CF are preserved, including DF.
; The final byte may be DS:FFFFh; no read may wrap to DS:0000h.
krs_crc32_update:
    pushfd
    push ebx
    push ecx
    push edx
    push esi
    cmp cx, 4096
    ja .bad
    movzx ebx, cx
    movzx edx, si
    add edx, ebx
    cmp edx, 0x10000
    ja .bad
    jcxz .done
.byte:
    mov bl, [si]
    inc si
    xor al, bl
    mov dl, 8
.bit:
    shr eax, 1
    jnc .next_bit
    xor eax, 0xedb88320
.next_bit:
    dec dl
    jnz .bit
    loop .byte
.done:
    pop esi
    pop edx
    pop ecx
    pop ebx
    popfd
    clc
    ret
.bad:
    pop esi
    pop edx
    pop ecx
    pop ebx
    popfd
    stc
    ret
