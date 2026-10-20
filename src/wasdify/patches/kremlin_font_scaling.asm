; Original WASDify code, MIT. NASM -f bin -DENG=0/1.
; BITS 16, in-place NE far-cdecl glyph method. No new loader relocations.
; The startup hook selects the native FONT8 resource in every video mode.
BITS 16
%if ENG
%define ORIGIN 0x153e
%else
%define ORIGIN 0x158
%endif
section .text vstart=ORIGIN align=1
    inc bp
    push bp
    mov bp,sp
    push ds
    push ss
    pop ds
    sub sp,16
    push si
    push di
    xor si,si                     ; destination row
.row:
    les bx,[bp+6]
    cmp si,[es:bx+0x24]
    jge .done
    mov ax,si
    shl ax,3
    xor dx,dx
    div word [es:bx+0x24]
    movzx dx,byte [bp+0x12]
    shl dx,3
    add ax,dx
    les bx,[es:bx+0x1a]
    add bx,ax
    mov al,[es:bx]
    mov [bp-4],al                 ; sampled source row
    xor di,di
.column:
    les bx,[bp+6]
    cmp di,[es:bx+0x22]
    jge .next_row
    mov ax,di
    shl ax,3
    xor dx,dx
    div word [es:bx+0x22]
    mov cx,ax
    mov al,0x80
    shr al,cl
    mov dl,[es:bx+0x1e]
    test [bp-4],al
    jnz .pixel
    cmp word [bp+0x14],1
    je .next_column
    mov dl,[es:bx+0x20]
.pixel:
    movzx eax,si
    add eax,[bp+0xe]
    cmp eax,[es:bx+0x3c]
    jl .next_column
    cmp eax,[es:bx+0x44]
    jg .next_column
    mov [bp-8],eax
    movzx eax,di
    add eax,[bp+0xa]
    cmp eax,[es:bx+0x38]
    jl .next_column
    cmp eax,[es:bx+0x40]
    jg .next_column
    push dx
    push dword [bp-8]
    push eax
    push dword [bp+6]
    mov bx,[es:bx]
    call far [bx+4]
    add sp,14
.next_column:
    inc di
    jmp .column
.next_row:
    inc si
    jmp .row
.done:
    pop di
    pop si
    lea sp,[bp-2]
    pop ds
    pop bp
    dec bp
    retf
    times 240-($-$$) db 0x90
; Called by the font loader after the FONT8 stream has been read.
; Surface +30h/+34h are unsigned dword width/height. Minimum one pixel.
metrics:
    les bx,[bp+6]
    mov eax,[es:bx+0x30]
    xor edx,edx
    mov ecx,40
    div ecx
    test ax,ax
    jnz .width
    inc ax
.width:
    mov [es:bx+0x22],ax
    mov eax,[es:bx+0x34]
    xor edx,edx
    mov ecx,25
    div ecx
    test ax,ax
    jnz .height
    inc ax
.height:
    mov [es:bx+0x24],ax
    ret
    times 340-($-$$) db 0x90
