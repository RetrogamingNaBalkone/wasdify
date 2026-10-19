; Original WASDify code, MIT. NASM -f bin -DENG=0/1.
; 16-bit NE method replacement, no new relocations. Existing loader-fixup
; bytes at +62h..85h are unreachable data. DS=SS as in the native method.
BITS 16
%if ENG
%define ORIGIN 0x0d99
%define SURFACE 0xb53c
%define BACKGROUND 0xa668
%define LIFE_RECT 0xa672
%define KEY_RECT 0xa67a
%define PRINT_POINTER 0x15bb
%else
%define ORIGIN 0x0a36
%define SURFACE 0xb48c
%define BACKGROUND 0xa5b8
%define LIFE_RECT 0xa5c2
%define KEY_RECT 0xa5ca
%define PRINT_POINTER 0x1258
%endif
%define HUD (LIFE_RECT+20)
%define CALLER (ORIGIN+0x319)
%define ROWS (SURFACE+0xb6)
section .text vstart=ORIGIN align=1
    inc bp
    push bp
    mov bp,sp
    push ds
    push ss
    pop ds
    sub sp,12
    push si
    push di
    ; Derive two equally sized fields from the native key field. The life
    ; rectangle's left edge stays stable across calls and viewport changes.
    les bx,[LIFE_RECT]
    mov si,[es:bx]
    les bx,[KEY_RECT]
    mov cx,[es:bx+8]
    mov dx,[es:bx+10]
    mov di,[es:bx+2]
    mov eax,[bp+10]
    cmp eax,[LIFE_RECT]
    je .life
    mov ax,cx
    shr ax,2
    add si,cx
    add si,ax
.life:
    les bx,[bp+10]
    mov [es:bx],si
    mov [es:bx+2],di
    mov [es:bx+8],cx
    mov [es:bx+10],dx
    add cx,si
    add dx,di
    mov [es:bx+4],cx
    mov [es:bx+6],dx
    cld
    push word 0
    push word [es:bx+6]
    jmp short .draw
    times 0x62-($-$$) db 0x90
    ; Retain all original x87-emulation fixups and the rounding call pointer.
    db 0x9b,0xdf,0x46,0xf8,0x8b,0x46,0xfc,0x89,0x46,0xf8
    db 0x9b,0xdf,0x46,0xf8,0x9b,0xde,0xf1,0x26,0x8b,0x07,0x89,0x46,0xf8
    db 0x9b,0xdf,0x46,0xf8,0x9b,0xde,0xc9,0x9a,0xff,0xff,0,0
.draw:
    ; The caller has already installed the HUD background and font colours.
    push word 0
    push word [es:bx+4]
    push word 0
    push word [es:bx+2]
    push word 0
    push word [es:bx]
    push dword [SURFACE]
    les bx,[SURFACE]
    mov bx,[es:bx]
    call far [bx+0x38]
    ; 3/8 of field height, original bitmap aspect ratio, horizontally centered.
    les bx,[bp+10]
    imul ax,[es:bx+10],3
    shr ax,3
    push ax
    mov cx,[es:bx+8]
    les bx,[bp+14]
    mul word [es:bx]
    div word [es:bx+2]
    push ax
    sub cx,ax
    mov ax,cx
    shr ax,1
    add si,ax
    push dword [bp+14]
    push word 0
    push di
    push word 0
    push si
    push dword [SURFACE]
    les bx,[SURFACE]
    mov bx,[es:bx]
    call far [bx+0x3c]
    call post
    ; Unsigned native current value (not a percentage), NUL-terminated.
    lea di,[bp-3]
    mov byte [di],0
    mov ax,[bp+20]
    mov cx,10
    xor si,si
.digit:
    xor dx,dx
    div cx
    add dl,'0'
    dec di
    mov [di],dl
    inc si
    test ax,ax
    jnz .digit
    ; Center using the active game font's actual glyph width.
    les bx,[SURFACE]
    imul si,[es:bx+0x22]
    les bx,[bp+10]
    mov ax,[es:bx+8]
    sub ax,si
    sar ax,1
    add ax,[es:bx]
    mov dx,[es:bx+10]
    shr dx,1
    add dx,[es:bx+2]
    push ss
    push di
    push dx
    push ax
    push dword [SURFACE]
    call far [cs:PRINT_POINTER]
    call dirty
    ; Discard native cdecl call arguments together, then restore saved SI/DI.
    lea sp,[bp-18]
    pop di
    pop si
    lea sp,[bp-2]
    pop ds
    pop bp
    dec bp
    retf
    times 330-($-$$) db 0x90

; Compact equivalent of the two native callers, leaving room for helpers.
; Original dirty-list far-pointer fixups stay exactly at +41h and +89h.
section .caller vstart=CALLER align=1
    ; The enclosing native method already checks that HUD state is dirty.
    xor si,si
.loop:
    les bx,[bp+6]
    add bx,si
    push si
    push word [es:bx+0x12]
    push si
    push dword [es:bx+0x65]
    push dword [si+LIFE_RECT]
    push eax                       ; unused HUD argument
    push cs
    call ORIGIN
    add sp,18
    add si,4
    cmp si,8
    jb .loop
    jmp short done
endrow:
    cmp dx,[bp-22]
    ja .end
    jmp near row
.end:
    ret
    times 0x33-($-$$) db 0x90
dirty:
    push dword [bp+10]
    mov eax,[SURFACE]
    add ax,2
    push eax
    db 0x9a,0xff,0xff,0,0
    add sp,8
    ret
post:
    ; Copy the actual neighbouring native window pillar, row by row. Obtain
    ; the huge-selector step from the loader-patched native pixel reader.
    les bx,[SURFACE]
    mov bx,[es:bx]
    les bx,[bx]
    mov cl,[es:bx+0x20]
    mov ax,1
    shl ax,cl
    mov [bp-4],ax
    les bx,[bp+10]
    mov ax,[es:bx+4]
    mov [bp-6],ax
    mov dx,[es:bx+2]
    mov cx,[es:bx+8]
    shr cx,2
    mov [bp-10],cx
    les bx,[KEY_RECT]
    mov ax,[es:bx]
    sub ax,cx
    inc ax
    mov [bp-8],ax
    jmp near row
    times 0x89-($-$$) db 0x90
    db 0xff,0xff,0,0
done:
    times 144-($-$$) db 0x90

; Equivalent compact setters provide space for the copy helper. Their
; exported entry offsets and native far-cdecl contracts remain unchanged.
section .life vstart=(ORIGIN+0x95f) align=1
    push si
    mov si,0x12
    mov cx,6
    jmp short setter
setter:
    push bp
    mov bp,sp
    les bx,[bp+8]
    or [es:bx+0x10],cx
    add bx,si
    mov ax,[bp+12]
    mov [es:bx],ax
    pop bp
    pop si
    retf
    times 33-($-$$) db 0x90
section .charge vstart=(ORIGIN+0x980) align=1
    push si
    mov si,0x16
    mov cx,10
    jmp short setter
row:
    mov bx,dx
    shl bx,2
    les bx,[bx+ROWS]
    push ds
    push es
    mov di,bx
    add di,[bp-6]
    jmp near destination
    times 33-($-$$) db 0x90
section .maximum vstart=(ORIGIN+0x9bd) align=1
    push si
    mov si,0x14
    mov cx,6
    jmp short setter
destination:
    jnc .ready
    mov ax,es
    add ax,[bp-4]
    mov es,ax
.ready:
    pop ds
    mov si,bx
    add si,[bp-8]
    jmp near source
    times 33-($-$$) db 0x90
section .maximum_charge vstart=(ORIGIN+0x9de) align=1
    push si
    mov si,0x18
    mov cx,10
    jmp short setter
source:
    jnc .ready
    mov ax,ds
    add ax,[bp-4]
    mov ds,ax
.ready:
    mov cx,[bp-10]
copy:
    lodsb
    stosb
    test si,si
    jnz short source_done
    mov ax,ds
    jmp near source_wrap
    times 33-($-$$) db 0x90
section .counter vstart=(ORIGIN+0x9ff) align=1
    push si
    mov si,0x22
    mov cx,0x402
    jmp near setter
source_wrap:
    add ax,[bp-4]
    mov ds,ax
source_done:
    test di,di
    jnz .ready
    mov ax,es
    add ax,[bp-4]
    mov es,ax
.ready:
    loop copy
    pop ds
    inc dx
    jmp near endrow
    times 34-($-$$) db 0x90
