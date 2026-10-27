; Military Drill 0.85 - the machine code of the five code caves, byte for byte as the battle script installs them.
; Written by "python lanes.py listing"; every build and check fails when the caves no longer match this file.
; Each cave starts with "movabs rax, <table>": the 8 zero bytes are the address of the mod's table, written in
; when the cave is installed (memreader Plus code_cave(site, cave)). A cave ends by running off its last byte:
; memreader Plus places the game instructions it replaced and the jump back into the game after it. Offsets are
; from the start of the cave.
; Each place is found in Warhammer3.exe by the byte pattern of the same name in sites.py (it must match exactly once).

; cave "place_send" (inside the game routine), 523 bytes
0000  48 b8 00 00 00 00 00 00 00 00        movabs rax, 0
000a  f0 ff 80 10 00 00 00                 lock inc dword ptr [rax + 0x10]
0011  49 8b 8c 24 e8 02 00 00              mov rcx, qword ptr [r12 + 0x2e8]
0019  48 85 c9                             test rcx, rcx
001c  0f 84 e9 01 00 00                    je 0x20b
0022  4c 8d 90 80 00 00 00                 lea r10, [rax + 0x80]
0029  4d 8b 1a                             mov r11, qword ptr [r10]
002c  4d 85 db                             test r11, r11
002f  0f 84 d6 01 00 00                    je 0x20b
0035  49 39 cb                             cmp r11, rcx
0038  74 09                                je 0x43
003a  49 81 c2 08 01 00 00                 add r10, 0x108
0041  eb e6                                jmp 0x29
0043  41 0f 10 45 00                       movups xmm0, xmmword ptr [r13]
0048  41 0f 11 82 60 00 00 00              movups xmmword ptr [r10 + 0x60], xmm0
0050  41 0f 10 45 10                       movups xmm0, xmmword ptr [r13 + 0x10]
0055  41 0f 11 82 70 00 00 00              movups xmmword ptr [r10 + 0x70], xmm0
005d  41 0f 10 45 20                       movups xmm0, xmmword ptr [r13 + 0x20]
0062  41 0f 11 82 80 00 00 00              movups xmmword ptr [r10 + 0x80], xmm0
006a  8b 4b 5c                             mov ecx, dword ptr [rbx + 0x5c]
006d  41 89 8a 90 00 00 00                 mov dword ptr [r10 + 0x90], ecx
0074  0f b6 4b 63                          movzx ecx, byte ptr [rbx + 0x63]
0078  41 89 8a 94 00 00 00                 mov dword ptr [r10 + 0x94], ecx
007f  41 ff 82 98 00 00 00                 inc dword ptr [r10 + 0x98]
0086  83 b8 00 00 00 00 00                 cmp dword ptr [rax], 0
008d  0f 84 78 01 00 00                    je 0x20b
0093  f3 41 0f 10 62 1c                    movss xmm4, dword ptr [r10 + 0x1c]
0099  0f 57 c0                             xorps xmm0, xmm0
009c  0f 2e e0                             ucomiss xmm4, xmm0
009f  0f 84 66 01 00 00                    je 0x20b
00a5  49 83 7d 18 00                       cmp qword ptr [r13 + 0x18], 0
00aa  0f 85 1a 01 00 00                    jne 0x1ca
00b0  41 8b 4a 28                          mov ecx, dword ptr [r10 + 0x28]
00b4  39 4c 24 28                          cmp dword ptr [rsp + 0x28], ecx
00b8  76 04                                jbe 0xbe
00ba  89 4c 24 28                          mov dword ptr [rsp + 0x28], ecx
00be  31 d2                                xor edx, edx
00c0  4d 8b 82 f8 00 00 00                 mov r8, qword ptr [r10 + 0xf8]
00c7  4d 85 c0                             test r8, r8
00ca  74 29                                je 0xf5
00cc  41 8b 8a 00 01 00 00                 mov ecx, dword ptr [r10 + 0x100]
00d3  85 c9                                test ecx, ecx
00d5  74 1e                                je 0xf5
00d7  81 f9 00 01 00 00                    cmp ecx, 0x100
00dd  77 16                                ja 0xf5
00df  4d 39 20                             cmp qword ptr [r8], r12
00e2  74 0e                                je 0xf2
00e4  49 83 c0 10                          add r8, 0x10
00e8  ff c9                                dec ecx
00ea  75 f3                                jne 0xdf
00ec  f0 ff 40 78                          lock inc dword ptr [rax + 0x78]
00f0  eb 03                                jmp 0xf5
00f2  4c 89 c2                             mov rdx, r8
00f5  48 85 d2                             test rdx, rdx
00f8  0f 85 d5 00 00 00                    jne 0x1d3
00fe  f3 41 0f 10 4d 00                    movss xmm1, dword ptr [r13]
0104  f3 41 0f 5c 4a 08                    subss xmm1, dword ptr [r10 + 8]
010a  f3 41 0f 10 55 08                    movss xmm2, dword ptr [r13 + 8]
0110  f3 41 0f 5c 52 0c                    subss xmm2, dword ptr [r10 + 0xc]
0116  f3 41 0f 59 4a 10                    mulss xmm1, dword ptr [r10 + 0x10]
011c  f3 41 0f 59 52 14                    mulss xmm2, dword ptr [r10 + 0x14]
0122  f3 0f 58 ca                          addss xmm1, xmm2
0126  0f 28 c1                             movaps xmm0, xmm1
0129  f3 41 0f 5c 82 ec 00 00 00           subss xmm0, dword ptr [r10 + 0xec]
0132  f3 41 0f 59 82 e8 00 00 00           mulss xmm0, dword ptr [r10 + 0xe8]
013b  f3 41 0f 5e 4a 18                    divss xmm1, dword ptr [r10 + 0x18]
0141  f3 0f 2d c9                          cvtss2si ecx, xmm1
0145  81 f9 00 00 00 80                    cmp ecx, 0x80000000
014b  0f 84 ba 00 00 00                    je 0x20b
0151  45 8b 5a 20                          mov r11d, dword ptr [r10 + 0x20]
0155  44 39 d9                             cmp ecx, r11d
0158  41 0f 4f cb                          cmovg ecx, r11d
015c  41 f7 db                             neg r11d
015f  44 39 d9                             cmp ecx, r11d
0162  41 0f 4c cb                          cmovl ecx, r11d
0166  f3 0f 2a c9                          cvtsi2ss xmm1, ecx
016a  f3 41 0f 58 4a 2c                    addss xmm1, dword ptr [r10 + 0x2c]
0170  f3 0f 59 cc                          mulss xmm1, xmm4
0174  f3 0f 58 c8                          addss xmm1, xmm0
0178  41 0f 10 45 00                       movups xmm0, xmmword ptr [r13]
017d  41 0f 11 42 30                       movups xmmword ptr [r10 + 0x30], xmm0
0182  41 0f 10 45 10                       movups xmm0, xmmword ptr [r13 + 0x10]
0187  41 0f 11 42 40                       movups xmmword ptr [r10 + 0x40], xmm0
018c  41 0f 10 45 20                       movups xmm0, xmmword ptr [r13 + 0x20]
0191  41 0f 11 42 50                       movups xmmword ptr [r10 + 0x50], xmm0
0196  4d 8d 6a 30                          lea r13, [r10 + 0x30]
019a  0f 28 d1                             movaps xmm2, xmm1
019d  f3 41 0f 59 52 10                    mulss xmm2, dword ptr [r10 + 0x10]
01a3  f3 41 0f 58 55 00                    addss xmm2, dword ptr [r13]
01a9  f3 41 0f 11 55 00                    movss dword ptr [r13], xmm2
01af  f3 41 0f 59 4a 14                    mulss xmm1, dword ptr [r10 + 0x14]
01b5  f3 41 0f 58 4d 08                    addss xmm1, dword ptr [r13 + 8]
01bb  f3 41 0f 11 4d 08                    movss dword ptr [r13 + 8], xmm1
01c1  f0 ff 80 08 00 00 00                 lock inc dword ptr [rax + 8]
01c8  eb 41                                jmp 0x20b
01ca  f0 ff 80 18 00 00 00                 lock inc dword ptr [rax + 0x18]
01d1  eb 38                                jmp 0x20b
01d3  41 0f 10 45 00                       movups xmm0, xmmword ptr [r13]
01d8  41 0f 11 42 30                       movups xmmword ptr [r10 + 0x30], xmm0
01dd  41 0f 10 45 10                       movups xmm0, xmmword ptr [r13 + 0x10]
01e2  41 0f 11 42 40                       movups xmmword ptr [r10 + 0x40], xmm0
01e7  41 0f 10 45 20                       movups xmm0, xmmword ptr [r13 + 0x20]
01ec  41 0f 11 42 50                       movups xmmword ptr [r10 + 0x50], xmm0
01f1  8b 4a 08                             mov ecx, dword ptr [rdx + 8]
01f4  41 89 4a 30                          mov dword ptr [r10 + 0x30], ecx
01f8  8b 4a 0c                             mov ecx, dword ptr [rdx + 0xc]
01fb  41 89 4a 38                          mov dword ptr [r10 + 0x38], ecx
01ff  4d 8d 6a 30                          lea r13, [r10 + 0x30]
0203  f0 ff 40 74                          lock inc dword ptr [rax + 0x74]
0207  f0 ff 40 08                          lock inc dword ptr [rax + 8]

; cave "recheck" (inside the game routine), 163 bytes
0000  48 b8 00 00 00 00 00 00 00 00        movabs rax, 0
000a  f0 ff 80 20 00 00 00                 lock inc dword ptr [rax + 0x20]
0011  83 b8 00 00 00 00 00                 cmp dword ptr [rax], 0
0018  0f 84 85 00 00 00                    je 0xa3
001e  48 8b 4b 08                          mov rcx, qword ptr [rbx + 8]
0022  48 85 c9                             test rcx, rcx
0025  0f 84 78 00 00 00                    je 0xa3
002b  83 79 24 00                          cmp dword ptr [rcx + 0x24], 0
002f  0f 84 6e 00 00 00                    je 0xa3
0035  48 8b 49 28                          mov rcx, qword ptr [rcx + 0x28]
0039  48 85 c9                             test rcx, rcx
003c  0f 84 61 00 00 00                    je 0xa3
0042  48 8b 09                             mov rcx, qword ptr [rcx]
0045  48 85 c9                             test rcx, rcx
0048  0f 84 55 00 00 00                    je 0xa3
004e  48 8b 89 e8 02 00 00                 mov rcx, qword ptr [rcx + 0x2e8]
0055  48 85 c9                             test rcx, rcx
0058  0f 84 45 00 00 00                    je 0xa3
005e  4c 8d 90 80 00 00 00                 lea r10, [rax + 0x80]
0065  4d 8b 1a                             mov r11, qword ptr [r10]
0068  4d 85 db                             test r11, r11
006b  0f 84 32 00 00 00                    je 0xa3
0071  49 39 cb                             cmp r11, rcx
0074  74 09                                je 0x7f
0076  49 81 c2 08 01 00 00                 add r10, 0x108
007d  eb e6                                jmp 0x65
007f  41 83 7a 24 00                       cmp dword ptr [r10 + 0x24], 0
0084  0f 84 19 00 00 00                    je 0xa3
008a  41 c7 42 24 00 00 00 00              mov dword ptr [r10 + 0x24], 0
0092  c7 83 88 00 00 00 00 24 74 c9        mov dword ptr [rbx + 0x88], 0xc9742400
009c  f0 ff 80 28 00 00 00                 lock inc dword ptr [rax + 0x28]

; cave "station" (inside the game routine), 175 bytes
0000  48 b8 00 00 00 00 00 00 00 00        movabs rax, 0
000a  83 b8 00 00 00 00 00                 cmp dword ptr [rax], 0
0011  0f 84 98 00 00 00                    je 0xaf
0017  83 b8 6c 00 00 00 00                 cmp dword ptr [rax + 0x6c], 0
001e  0f 84 8b 00 00 00                    je 0xaf
0024  48 8b 8f e8 02 00 00                 mov rcx, qword ptr [rdi + 0x2e8]
002b  48 85 c9                             test rcx, rcx
002e  0f 84 7b 00 00 00                    je 0xaf
0034  4c 8d 90 80 00 00 00                 lea r10, [rax + 0x80]
003b  4d 8b 1a                             mov r11, qword ptr [r10]
003e  4d 85 db                             test r11, r11
0041  0f 84 68 00 00 00                    je 0xaf
0047  49 39 cb                             cmp r11, rcx
004a  74 09                                je 0x55
004c  49 81 c2 08 01 00 00                 add r10, 0x108
0053  eb e6                                jmp 0x3b
0055  41 83 ba 9c 00 00 00 00              cmp dword ptr [r10 + 0x9c], 0
005d  0f 84 4c 00 00 00                    je 0xaf
0063  49 8b 92 a0 00 00 00                 mov rdx, qword ptr [r10 + 0xa0]
006a  48 85 d2                             test rdx, rdx
006d  0f 84 3c 00 00 00                    je 0xaf
0073  41 8b 8a a8 00 00 00                 mov ecx, dword ptr [r10 + 0xa8]
007a  85 c9                                test ecx, ecx
007c  0f 84 2d 00 00 00                    je 0xaf
0082  48 39 3a                             cmp qword ptr [rdx], rdi
0085  74 08                                je 0x8f
0087  48 83 c2 40                          add rdx, 0x40
008b  ff c9                                dec ecx
008d  eb eb                                jmp 0x7a
008f  48 c7 02 00 00 00 00                 mov qword ptr [rdx], 0
0096  f0 41 ff 8a 9c 00 00 00              lock dec dword ptr [r10 + 0x9c]
009e  f0 ff 80 40 00 00 00                 lock inc dword ptr [rax + 0x40]
00a5  48 83 c2 08                          add rdx, 8
00a9  48 89 f9                             mov rcx, rdi
00ac  ff 50 30                             call qword ptr [rax + 0x30]

; cave "walk_to" (at the entry of the game routine), 555 bytes
0000  48 b8 00 00 00 00 00 00 00 00        movabs rax, 0
000a  83 b8 00 00 00 00 00                 cmp dword ptr [rax], 0
0011  0f 84 14 02 00 00                    je 0x22b
0017  83 b8 64 00 00 00 00                 cmp dword ptr [rax + 0x64], 0
001e  0f 84 07 02 00 00                    je 0x22b
0024  4c 8b 14 24                          mov r10, qword ptr [rsp]
0028  4c 3b 90 48 00 00 00                 cmp r10, qword ptr [rax + 0x48]
002f  0f 85 f6 01 00 00                    jne 0x22b
0035  4c 8b 99 e8 02 00 00                 mov r11, qword ptr [rcx + 0x2e8]
003c  4d 85 db                             test r11, r11
003f  0f 84 e6 01 00 00                    je 0x22b
0045  4c 8d 90 80 00 00 00                 lea r10, [rax + 0x80]
004c  49 83 3a 00                          cmp qword ptr [r10], 0
0050  0f 84 d5 01 00 00                    je 0x22b
0056  4d 39 1a                             cmp qword ptr [r10], r11
0059  74 09                                je 0x64
005b  49 81 c2 08 01 00 00                 add r10, 0x108
0062  eb e8                                jmp 0x4c
0064  f0 ff 80 58 00 00 00                 lock inc dword ptr [rax + 0x58]
006b  f3 41 0f 10 aa b0 00 00 00           movss xmm5, dword ptr [r10 + 0xb0]
0074  0f 57 c0                             xorps xmm0, xmm0
0077  0f 2e e8                             ucomiss xmm5, xmm0
007a  0f 84 ab 01 00 00                    je 0x22b
0080  f3 0f 10 0a                          movss xmm1, dword ptr [rdx]
0084  f3 41 0f 5c 8a b4 00 00 00           subss xmm1, dword ptr [r10 + 0xb4]
008d  f3 0f 10 52 08                       movss xmm2, dword ptr [rdx + 8]
0092  f3 41 0f 5c 92 b8 00 00 00           subss xmm2, dword ptr [r10 + 0xb8]
009b  0f 28 c1                             movaps xmm0, xmm1
009e  f3 41 0f 59 82 c4 00 00 00           mulss xmm0, dword ptr [r10 + 0xc4]
00a7  0f 28 e2                             movaps xmm4, xmm2
00aa  f3 41 0f 59 a2 c8 00 00 00           mulss xmm4, dword ptr [r10 + 0xc8]
00b3  f3 0f 58 c4                          addss xmm0, xmm4
00b7  41 0f 2f 82 cc 00 00 00              comiss xmm0, dword ptr [r10 + 0xcc]
00bf  0f 82 66 01 00 00                    jb 0x22b
00c5  41 0f 2f 82 d0 00 00 00              comiss xmm0, dword ptr [r10 + 0xd0]
00cd  0f 87 58 01 00 00                    ja 0x22b
00d3  f3 41 0f 59 8a bc 00 00 00           mulss xmm1, dword ptr [r10 + 0xbc]
00dc  f3 41 0f 59 92 c0 00 00 00           mulss xmm2, dword ptr [r10 + 0xc0]
00e5  f3 0f 58 ca                          addss xmm1, xmm2
00e9  41 0f 2f 8a e0 00 00 00              comiss xmm1, dword ptr [r10 + 0xe0]
00f1  0f 82 34 01 00 00                    jb 0x22b
00f7  41 0f 2f 8a e4 00 00 00              comiss xmm1, dword ptr [r10 + 0xe4]
00ff  0f 87 26 01 00 00                    ja 0x22b
0105  0f 28 c1                             movaps xmm0, xmm1
0108  f3 0f 5e c5                          divss xmm0, xmm5
010c  f3 0f 5c 80 60 00 00 00              subss xmm0, dword ptr [rax + 0x60]
0114  f3 44 0f 2d d8                       cvtss2si r11d, xmm0
0119  41 81 fb 00 00 00 80                 cmp r11d, 0x80000000
0120  0f 84 05 01 00 00                    je 0x22b
0126  45 3b 9a dc 00 00 00                 cmp r11d, dword ptr [r10 + 0xdc]
012d  7e 07                                jle 0x136
012f  45 8b 9a dc 00 00 00                 mov r11d, dword ptr [r10 + 0xdc]
0136  45 3b 9a d8 00 00 00                 cmp r11d, dword ptr [r10 + 0xd8]
013d  7d 07                                jge 0x146
013f  45 8b 9a d8 00 00 00                 mov r11d, dword ptr [r10 + 0xd8]
0146  45 85 db                             test r11d, r11d
0149  79 04                                jns 0x14f
014b  f0 ff 40 70                          lock inc dword ptr [rax + 0x70]
014f  f3 41 0f 2a c3                       cvtsi2ss xmm0, r11d
0154  f3 0f 58 80 60 00 00 00              addss xmm0, dword ptr [rax + 0x60]
015c  f3 0f 59 c5                          mulss xmm0, xmm5
0160  0f 28 d1                             movaps xmm2, xmm1
0163  f3 0f 5c d0                          subss xmm2, xmm0
0167  f3 41 0f 59 92 d4 00 00 00           mulss xmm2, dword ptr [r10 + 0xd4]
0170  f3 0f 58 d0                          addss xmm2, xmm0
0174  f3 0f 5c d1                          subss xmm2, xmm1
0178  0f 28 e2                             movaps xmm4, xmm2
017b  0f 28 c2                             movaps xmm0, xmm2
017e  f3 41 0f 59 82 bc 00 00 00           mulss xmm0, dword ptr [r10 + 0xbc]
0187  f3 0f 58 02                          addss xmm0, dword ptr [rdx]
018b  f3 0f 11 02                          movss dword ptr [rdx], xmm0
018f  f3 41 0f 59 92 c0 00 00 00           mulss xmm2, dword ptr [r10 + 0xc0]
0198  f3 0f 58 52 08                       addss xmm2, dword ptr [rdx + 8]
019d  f3 0f 11 52 08                       movss dword ptr [rdx + 8], xmm2
01a2  f3 0f 10 02                          movss xmm0, dword ptr [rdx]
01a6  f3 0f 5c 81 88 00 00 00              subss xmm0, dword ptr [rcx + 0x88]
01ae  f3 41 0f 59 82 c4 00 00 00           mulss xmm0, dword ptr [r10 + 0xc4]
01b7  f3 0f 10 4a 08                       movss xmm1, dword ptr [rdx + 8]
01bc  f3 0f 5c 89 90 00 00 00              subss xmm1, dword ptr [rcx + 0x90]
01c4  f3 41 0f 59 8a c8 00 00 00           mulss xmm1, dword ptr [r10 + 0xc8]
01cd  f3 0f 58 c1                          addss xmm0, xmm1
01d1  0f 57 c9                             xorps xmm1, xmm1
01d4  f3 0f 5c cc                          subss xmm1, xmm4
01d8  f3 0f 5f cc                          maxss xmm1, xmm4
01dc  f3 41 0f 59 8a f0 00 00 00           mulss xmm1, dword ptr [r10 + 0xf0]
01e5  0f 28 d0                             movaps xmm2, xmm0
01e8  f3 0f 5c d1                          subss xmm2, xmm1
01ec  f3 41 0f 5f 92 f4 00 00 00           maxss xmm2, dword ptr [r10 + 0xf4]
01f5  f3 0f 5d d0                          minss xmm2, xmm0
01f9  f3 0f 5c d0                          subss xmm2, xmm0
01fd  0f 28 c2                             movaps xmm0, xmm2
0200  f3 41 0f 59 82 c4 00 00 00           mulss xmm0, dword ptr [r10 + 0xc4]
0209  f3 0f 58 02                          addss xmm0, dword ptr [rdx]
020d  f3 0f 11 02                          movss dword ptr [rdx], xmm0
0211  f3 41 0f 59 92 c8 00 00 00           mulss xmm2, dword ptr [r10 + 0xc8]
021a  f3 0f 58 52 08                       addss xmm2, dword ptr [rdx + 8]
021f  f3 0f 11 52 08                       movss dword ptr [rdx + 8], xmm2
0224  f0 ff 80 50 00 00 00                 lock inc dword ptr [rax + 0x50]

; cave "ghost_pass" (inside the game routine), 113 bytes
0000  48 b8 00 00 00 00 00 00 00 00        movabs rax, 0
000a  8b 88 80 7d 00 00                    mov ecx, dword ptr [rax + 0x7d80]
0010  85 c9                                test ecx, ecx
0012  74 5d                                je 0x71
0014  83 f9 20                             cmp ecx, 0x20
0017  77 58                                ja 0x71
0019  49 8b 97 80 00 00 00                 mov rdx, qword ptr [r15 + 0x80]
0020  49 3b 96 80 00 00 00                 cmp rdx, qword ptr [r14 + 0x80]
0027  75 48                                jne 0x71
0029  49 8b 97 e8 02 00 00                 mov rdx, qword ptr [r15 + 0x2e8]
0030  4d 8b 86 e8 02 00 00                 mov r8, qword ptr [r14 + 0x2e8]
0037  48 85 d2                             test rdx, rdx
003a  74 35                                je 0x71
003c  4d 85 c0                             test r8, r8
003f  74 30                                je 0x71
0041  4c 39 c2                             cmp rdx, r8
0044  74 2b                                je 0x71
0046  4c 8d 88 a0 7d 00 00                 lea r9, [rax + 0x7da0]
004d  4d 8b 11                             mov r10, qword ptr [r9]
0050  49 39 d2                             cmp r10, rdx
0053  74 0f                                je 0x64
0055  4d 39 c2                             cmp r10, r8
0058  74 0a                                je 0x64
005a  49 83 c1 08                          add r9, 8
005e  ff c9                                dec ecx
0060  75 eb                                jne 0x4d
0062  eb 0d                                jmp 0x71
0064  f0 ff 80 88 7d 00 00                 lock inc dword ptr [rax + 0x7d88]
006b  ff a0 90 7d 00 00                    jmp qword ptr [rax + 0x7d90]
