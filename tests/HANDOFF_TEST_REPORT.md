# รายงานทดสอบระบบเต็มรูปแบบ · AiT-test v1.0.0

> ทดสอบในฐานะผู้ใช้จริง: รัน CLI ทุกคำสั่งที่เอกสารสั่ง, รัน MCP ผ่าน JSON-RPC ดิบ, ติดตั้ง MCP เข้า opencode จริง, ทดสอบไฟล์ encoding แปลก ๆ, cache พัง, และ interrupt
> ทุกผลลัพธ์ในรายงานนี้มาจากการรันจริง ไม่มีการเดา. fixture และสคริปต์ช่วยทดสอบอยู่ใน `%TEMP%\opencode\qa-ait\` ไม่ได้อยู่ใน repo

---

## 1. บทสรุปผู้บริหาร

| หัวข้อ | ผล |
|---|---|
| วันที่ทดสอบ | 2026-10-24 |
| เป้าหมาย | `L:\.Tool\AiT-Test` (ait-test 1.0.0) |
| Test case ที่รัน | **94** |
| ผ่าน | **78** (83.0%) |
| ไม่ผ่าน | **15** (16.0%) |
| INFO (ไม่นับผ่าน/ไม่ผ่าน) | 1 |
| Blocked | 0 |
| ชุดทดสอบใน repo | `unittest` **250 tests → OK** (Windows 3.12.10 6.65s · WSL 3.14.4 13.72s) |
| สถานะโดยรวม | **REQUIRES REMEDIATION** ก่อนปล่อย v1.0.0 |
| สถานะหลังแก้ (รอบที่ 2) | **READY** — DEF-001..011 ปิดครบ, DOC-001..004 แก้แล้ว, `unittest` **268 tests OK** ทุก 8 interpreter (§10) |

**ข้อสรุปสำคัญ**

engine ทำงานตรงตามสัญญาหลักเกือบทั้งหมด: exit code 0/1/2/3/4 ถูกทุกกรณี, 13 กฎยิงได้ครบ, MCP protocol ถูกต้องตาม JSON-RPC, cache invalidate ด้วย digest จริง, encoding cp874/CRLF/BOM อ่านและเขียนกลับได้, self-scan สะอาด 0/0/0

แต่มี **3 ข้อบกพร่องระดับ MAJOR** ที่ต้องแก้ก่อน release:

1. `--fix` เขียน **BOM** ใส่ไฟล์ UTF-8 ที่ไม่มี BOM ทุกไฟล์ (แก้ byte นอกจุดที่ patch)
2. `--lf` **เงียบและคืน exit 0** เมื่อ cache ว่าง/เสียหาย → CI เขียวทั้งที่ไม่ได้สแกนอะไรเลย
3. กฎ `AIT-R01` **พลาด read-modify-write รูปแบบ `x = x + 1`** ทั้งที่เอกสารสัญญาไว้ และ `+=` ยิงปกติ

ที่เหลือเป็น MINOR ด้าน UX/ความสอดคล้องของข้อความ 8 ข้อ กับเอกสาร 4 จุด

> **อัปเดตรอบที่ 2 (แก้แล้ว):** ทั้ง 11 ข้อบกพร่องและเอกสารอีก 4 จุดถูกแก้ในโค้ดจริง พร้อมเพิ่ม regression test 18 ตัว (`unittest` 250 → **268 tests OK** ทุก 8 interpreter) หลักฐานก่อน/หลังแยกทีละข้ออยู่ใน §10

---

## 2. สภาพแวดล้อมและวิธีการ

| องค์ประกอบ | รายละเอียด |
|---|---|
| OS | Windows 11 (PowerShell 7) + WSL2 Ubuntu |
| Interpreter | CPython 3.12.10 (ค่าเริ่มต้น), 3.12.14, 3.13, 3.14, 3.15.0rc2, uv 3.10.21 / 3.11.16, WSL 3.14.4 |
| venv แยก | `qa-ait\venv` (editable install + ruff 0.17.2 + bandit 1.9.4), `qa-ait\venv2` (wheel clean-room) |
| MCP | opencode local server ชื่อ `ait` (`opencode.jsonc`) + MCP client ดิบผ่าน stdio |
| Fixture | 26 ไฟล์ใน `qa-ait\ws\` (clean/defect/mixed/enc/thai path/ignore/suppress/fix/syntax/big/empty/readonly) |
| ของจริงที่ใช้ทดสอบ | `tests/fixtures/`, `src/` (23 ไฟล์ 6,879 บรรทัด), `readme-th.md` |

**วิธีเก็บหลักฐาน**

- ทุกคำสั่ง CLI รันแยกทีละคำสั่ง เก็บ `stdout`, `stderr`, exit code แยกไฟล์ (ไม่ผ่าน pipe ที่กลืน exit code)
- สคริปต์ที่ใช้มีเฉพาะงานที่ shell ทำไม่ได้: ตรวจ byte ระดับ encoding, จับเวลา, ส่ง SIGINT, และต่อ pty ผ่าน `script` เพื่อทดสอบ `--fix` แบบ interactive สคริปต์เหล่านี้ไม่กลืน exception และพิมพ์ค่าที่วัดได้ดิบ ๆ
- MCP ทดสอบ 2 ทาง: client JSON-RPC ที่ส่ง frame เอง กับเรียกผ่าน harness ของ opencode จริง (3 tools)
- `--fix` รันกับสำเนาใน `%TEMP%` เท่านั้น ไม่แตะ source ของ repo

---

## 3. ตารางทดสอบ

### 3.1 CLI core

| ID | คำสั่ง | คาดหวัง | ผลจริง | Exit | ผล |
|---|---|---|---|---|---|
| TC-001 | `python cli.py --help` | usage ครบทุก flag | ครบ 9 flag + positional | 0 | PASS |
| TC-002 | `python cli.py --version` | `ait-engine 1.0.0` | `ait-engine 1.0.0` | 0 | PASS |
| TC-003 | `python cli.py src --no-cache` | 0 finding | `0/0/0` | 0 | PASS |
| TC-004 | `python -S cli.py src --no-cache` | ไม่ใช้ site-packages | `0/0/0` | 0 | PASS |
| TC-005 | `python cli.py tests/fixtures` | exit 1 + มี DEFECT | 11 DEFECT / 4 WARNING / 1 ADVICE | 1 | PASS |
| TC-006 | `python cli.py demo.py` (โค้ดจาก README บรรทัด 95–106) | N01 ที่ 10:10 | N01 ที่ 10:10 + causation 3 step | 1 | PASS |
| TC-007 | `-q` | เหลือ summary | 4 บรรทัด | 1 | PASS |
| TC-008 | `-v` | รายละเอียดต่อไฟล์ | มี per-file detail | 0 | PASS |
| TC-009 | `-x` | หยุดที่ DEFECT แรก | 1 block (A01) | 1 | PASS |
| TC-010 | `--maxfail 1` | 1 block แต่ tally ครบ | 1 block, tally 7/2/2 | 1 | PASS |
| TC-011 | `--maxfail 0` | ไม่มี block | ไม่มี block, tally ครบ | 0 | PASS |
| TC-012 | `--maxfail 999999999999999999999` | รับ big int | ทำงานปกติ | 0 | PASS |
| TC-013 | `python cli.py . --no-cache` | เก็บเฉพาะ .py ที่ควรเก็บ | 52 ไฟล์ = 23 src + 27 tests + 2 root | 1 | PASS |

### 3.2 Boundary และ negative input

| ID | คำสั่ง | คาดหวัง | ผลจริง | Exit | ผล |
|---|---|---|---|---|---|
| TC-014 | `--maxfail -1` | ปฏิเสธ | stderr `ait: --maxfail must be zero or positive` | 4 | PASS |
| TC-015 | `--maxfail abc` | ปฏิเสธ | stderr argparse `invalid int value` | 4 | PASS |
| TC-016 | `--maxfail 1.5` | ปฏิเสธ | stderr argparse `invalid int value` | 4 | PASS |
| TC-017 | `--nope` | ปฏิเสธ | stderr `unrecognized arguments: --nope` | 4 | PASS |
| TC-018 | target ที่ไม่มีอยู่ | ปฏิเสธ | `ERROR: Target is not accessible: ...` | 4 | PASS |
| TC-019 | `python cli.py ""` | ปฏิเสธ (usage error) | สแกน cwd 52 ไฟล์ | 1 | **FAIL** |
| TC-020 | ไฟล์ syntax ผิด | exit 3 ไม่มี traceback | `COLLECTION ERRORS` + `SyntaxError` | 3 | PASS |
| TC-021 | `--lf --no-cache` | ปฏิเสธ | stderr `ait: --lf needs the cache index; drop --no-cache` | 4 | PASS |
| TC-022 | `-q -v` พร้อมกัน | ระบุชัดว่าอะไรชนะ | `-q` ชนะเงียบ ๆ | 1 | INFO |
| TC-023 | `-x` + `--fix` บนไฟล์เดียว (non-TTY) | ไม่ crash, exit 1 | 1 block, exit 1, ไม่มี traceback | 1 | PASS |

### 3.3 Encoding และ filesystem

| ID | เคส | คาดหวัง | ผลจริง | Exit | ผล |
|---|---|---|---|---|---|
| TC-024 | สแกน cp874 (ไทย + N01) | อ่านได้ เจอ N01 | เจอ N01 | 1 | PASS |
| TC-025 | สแกน UTF-8 + BOM | อ่านได้ เจอ N01 | เจอ N01 | 1 | PASS |
| TC-026 | สแกน CRLF | อ่านได้ เจอ N01 | เจอ N01 | 1 | PASS |
| TC-027 | path/ชื่อไฟล์ไทย | ทำงานได้ | เจอ finding ปกติ | 1 | PASS |
| TC-028 | `--fix` ไฟล์ cp874 (pty) | ยังเป็น cp874, ไทยไม่เพี้ยน | cp874 + ไทยครบ + guard ถูก | 0 | PASS |
| TC-029 | `--fix` ไฟล์ BOM (pty) | BOM เดียว คงอยู่ | BOM เดียว คงอยู่ | 0 | PASS |
| TC-030 | `--fix` ไฟล์ CRLF (pty) | CRLF คงอยู่ | CRLF คงอยู่ | 0 | PASS |
| TC-031 | `--fix` ไฟล์ UTF-8 ไม่มี BOM | **byte เดิมนอก patch** | ได้ BOM เพิ่มที่ byte 0 | 1 | **FAIL** |
| TC-032 | `_encode_like('x = 1\n', b'x = 1\n')` | คืน byte เดิม | `b'\xef\xbb\xbfx = 1\n'` | - | **FAIL** |
| TC-033 | ไฟล์ว่าง 0 byte | ข้าม/ไม่ crash | `1 passed`, ไม่มี finding | 0 | PASS |
| TC-034 | `.aitignore` (`legacy_*.py`, `build/`) | เหลือ 1 ไฟล์ | collecting 1 ไฟล์ | 1 | PASS |

### 3.4 สัญญาของกฎ

| ID | เคส | คาดหวัง | ผลจริง | Exit | ผล |
|---|---|---|---|---|---|
| TC-035 | ครบ 13 กฎใน corpus | ยิงได้ทุก ID | 13/13 (H02 มาเป็น hint) | 1 | PASS |
| TC-036 | R01: `self.total += 1` | R01 | R01 | 1 | PASS |
| TC-037 | R01: `counter = counter + 1` + 4 thread | R01 | ไม่รายงาน | 0 | **FAIL** |
| TC-038 | R01: `self.total = self.total + 1` + lock | R01 | ไม่รายงาน | 0 | **FAIL** |
| TC-039 | H02: 6 ชั้น | รายงาน | รายงาน แต่บอก "5 levels deep" | 0 | **FAIL** |
| TC-040 | H02: 5 ชั้น | รายงาน (เอกสาร: เกิน 4) | ไม่รายงาน | 0 | **FAIL** |
| TC-041 | N01: attribute บน Optional | N01 | N01 | 1 | PASS |
| TC-042 | N01: `value + 1` บน `Optional[int]` | ควรจับได้ | ไม่รายงาน | 0 | **FAIL** |
| TC-043 | suppression `# ait: disable=AIT-N01` | หาย + นับ 1 suppressed | `1 suppressed` | 0 | PASS |
| TC-044 | suppression + finding อื่น | exit 1 + นับ suppressed | exit 1 + นับถูก | 1 | PASS |

### 3.5 Cache, `--lf`, state

| ID | เคส | คาดหวัง | ผลจริง | Exit | ผล |
|---|---|---|---|---|---|
| TC-045 | `--no-cache` 2 รอบ | ไม่สร้าง cache | ไม่มี `.ait_cache` | 1 | PASS |
| TC-046 | แก้ไฟล์ clean → defect | เห็น finding ใหม่ | เห็น N01 ทันที | 1 | PASS |
| TC-047 | แก้ไฟล์ defect → clean | หาย | `0/0/0` | 0 | PASS |
| TC-048 | รันซ้ำ 3 รอบ | ผลนิ่ง | 7/2/2 ทุกรอบ, hash ไฟล์เท่าเดิม | 1 | PASS |
| TC-049 | `--lf` หลังรอบที่ fail | สแกนเฉพาะไฟล์ที่ fail | 1 ไฟล์, `cached 1/1` | 1 | PASS |
| TC-050 | `--lf` cache เป็น directory | full scan + stderr | full scan + `ait: --lf needs a usable cache; ...` | 1 | PASS |
| TC-051 | `--lf` cache 0 byte | fallback + แจ้ง | `collecting 0 files`, เงียบ | 0 | **FAIL** |
| TC-052 | `--lf` cache 14 byte ขยะ | fallback + แจ้ง | `0 failed, 0 passed`, เงียบ | 0 | **FAIL** |
| TC-053 | `--lf` cache ถูกลบ | fallback + แจ้ง | `collecting 0 files`, เงียบ | 0 | **FAIL** |
| TC-054 | SIGINT กลางการสแกน | exit 2 ไม่มี traceback | `ERROR: interrupted by user` | 2 | PASS |

### 3.6 `--fix`

| ID | เคส | คาดหวัง | ผลจริง | Exit | ผล |
|---|---|---|---|---|---|
| TC-055 | non-TTY (`< NUL`) | แจ้งว่าไม่มี input | พิมพ์ prompt ค้าง ๆ แล้ว `fixes applied: 0` | 1 | **FAIL** |
| TC-056 | TTY ตอบ `y` (pty) | patch + backup + rescan | 1 applied, backup, rescan 0 | 0 | PASS |
| TC-057 | หลาย finding ตอบ `a` | apply จากล่างขึ้นบน | 2 applied (M01, N01), M02 เหลือ | 1 | PASS |
| TC-058 | รัน `--fix` ซ้ำ | ไม่แก้อะไรเพิ่ม | ไฟล์เท่าเดิม ไม่มี backup เพิ่ม | 1 | PASS |
| TC-059 | artefact หลัง fix | ไม่มี `.ait-tmp` ค้าง | ไม่มี | - | PASS |
| TC-060 | backup เก็บไฟล์ก่อนแก้ | เป็น byte เดิมก่อน patch | `fix\.ait_backup\20261024-085830-455\fixme_wsl.py` ไม่มี guard และเท่ากับ fixture ทุก byte | - | PASS |

### 3.7 MCP

| ID | เคส | คาดหวัง | ผลจริง | ผล |
|---|---|---|---|---|
| TC-061 | `initialize` | serverInfo + capability tools | `ait-engine 1.0.0`, `tools.listChanged=false` | PASS |
| TC-062 | `tools/list` | 3 tools, schema ปิด | 3 tools, `additionalProperties=false` | PASS |
| TC-063 | `ait_review` demo | 1 finding, `isError:false` | 1 finding, exit_code 1 | PASS |
| TC-064 | `ait_review` clean | 0 finding | 0 finding, exit 0 | PASS |
| TC-065 | `severity_min=DEFECT` | เฉพาะ DEFECT | 7 findings / 14 ไฟล์ | PASS |
| TC-066 | `max_findings=1` | ตัดผลแต่ tally ครบ | 1 returned, tally ยังครบ | PASS |
| TC-067 | `max_findings` 0 / 501 | `-32602` | `-32602` | PASS |
| TC-068 | `ait_slice_context` depth 0 vs 3 | 0 สั้นกว่า 3 | 9,244 vs 18,276 byte | PASS |
| TC-069 | slice symbol ไม่มี / ไฟล์ไม่มี | `-32602` ชัดเจน | ข้อความบอกสาเหตุตรง | PASS |
| TC-070 | `ait_verify_fix` guarded | resolved N01 | `resolved=[N01]`, `clean=true` | PASS |
| TC-071 | `ait_verify_fix` regressing | remaining N01 | `remaining_defects=[N01]` | PASS |
| TC-072 | candidate syntax พัง | `isError:true` | `isError:true` | PASS |
| TC-073 | unknown tool / arg | `-32602` | `-32602` | PASS |
| TC-074 | unknown method | `-32601` | `-32601` | PASS |
| TC-075 | JSON เสีย / ไม่ใช่ object | `-32700` / `-32600` | ตรงทั้งคู่ | PASS |
| TC-076 | `shutdown` + exit | process จบ 0 | exit 0, stderr 1 บรรทัด | PASS |
| TC-077 | review ไฟล์ syntax ผิด | exit_code 3, `isError:false` | ตรง | PASS |
| TC-078 | review `exitfirst` | 1 finding | 1 finding | PASS |
| TC-079 | review ไฟล์ที่ suppress | `suppressed: 1` | `suppressed: 1` | PASS |
| TC-080 | เรียกผ่าน opencode จริง (`tools.ait.*`) | 3 tools ใช้ได้ | ใช้ได้ครบ | PASS |

### 3.8 Packaging, CI, เอกสาร

| ID | เคส | คาดหวัง | ผลจริง | ผล |
|---|---|---|---|---|
| TC-081 | `pip install -e .` ใน venv สะอาด | ติดตั้งได้ | `ait-test 1.0.0`, `Requires:` ว่าง | PASS |
| TC-082 | `ait src` จาก editable install | exit 0 | 0/0/0, 23 ไฟล์ | PASS |
| TC-083 | `pip wheel` + ติดตั้ง `--no-index` | รันได้นอก source tree | `ait --version` 0, scan 0, broken 3, `ait-mcp` 0, `requires None` | PASS |
| TC-084 | `unittest discover` | 250 OK | 250 OK 6.65s | PASS |
| TC-085 | `-W error::DeprecationWarning` | 250 OK | 250 OK 6.05s | PASS |
| TC-086 | ruff 0.17.2 | 0 findings | `All checks passed!` | PASS |
| TC-087 | bandit 1.9.4 `-c pyproject.toml -ll` | exit 0 | exit 0 | PASS |
| TC-088 | `compileall src cli.py mcp.py scripts` | compile ทุก module | `Can't list 'scripts'` แต่ exit 0 | **FAIL** |
| TC-089 | `ait-mcp --help` | มี help | เงียบ exit 0 | **FAIL** |
| TC-090 | ตัวเลขใน README: 250 tests / 23 ไฟล์ / 6,879 บรรทัด / 13 rules / 15 job runs | ตรง | ตรงทั้งหมด | PASS |
| TC-091 | ตาราง perf: 618 ms cold / 11.9 ms warm | ใกล้เคียง | 670 ms / 12.5 ms | PASS |
| TC-092 | WSL 3.14.4: src / fixtures / missing / syntax | 0 / 1 / 4 / 3 | ตรงทุกค่า, stderr ว่าง | PASS |
| TC-093 | color: TTY / `NO_COLOR` / `--no-color` / pipe | 19 / 0 / 0 / 0 escape | ตรง | PASS |
| TC-094 | README: "patch ทุก finding" | patch ครบทุก finding | 5 จาก 11 finding มี patch (A01, A02, H02, M02, R01 ไม่มี) | **FAIL** |

---

## 4. ตารางข้อบกพร่อง

| ID | ระดับ | จุดที่ผิด | อาการ | root cause | วิธีแก้ที่แนะนำ |
|---|---|---|---|---|---|
| DEF-001 | **MAJOR** | `--fix` / `_encode_like` | ไฟล์ UTF-8 ที่ไม่มี BOM ถูกเติม `EF BB BF` ที่ byte 0 | `SOURCE_ENCODINGS` เริ่มด้วย `utf-8-sig` และ `resolve_encoding()` คืน `utf-8-sig` ให้ไฟล์ที่ไม่มี BOM ได้ (sig decoder รับได้ทั้งคู่) แล้ว `text.encode("utf-8-sig")` เติม BOM เอง | เช็ค `raw.startswith(b"\xef\xbb\xbf")` ใน `_encode_like` แล้ว encode เป็น `utf-8` เมื่อไม่มี BOM |
| DEF-002 | **MAJOR** | `--lf` + cache ว่าง/เสีย | `collecting 0 files`, exit 0, stderr ว่าง | `CacheStore.last_failed()` คืน `[]` ทั้งกรณี "ไม่มีไฟล์ fail" และ "ไม่มี index/sqlite error" CLI จึงตัด `files` เหลือศูนย์โดยไม่รู้ตัว | ถ้า index ว่างหรือ `indexed == 0` ให้แจ้ง stderr แล้ว full scan เหมือนกรณี cache เปิดไม่ได้ |
| DEF-003 | **MAJOR** | `race_hazards.py` | `x = x + 1` และ `self.x = self.x + 1` ไม่ถูกจับ (`+=` ถูกจับ) | `_RACY_WRITE_KINDS = {augassign, mutate, itemwrite}` ไม่มี Assign ที่ RHS อ่าน target เดิม | classify `Assign` ที่ RHS มี Load ของ target เดียวกันเป็น racy แล้วใช้ evidence logic เดิม |
| DEF-004 | MINOR | `hygiene.py` (H02) | 5 ชั้นเงียบ, 6 ชั้นรายงานว่า "5 levels" | นับ depth ของ node (ไม่ใช่ body) แล้วเทียบ `> 4` | นับ body depth หรือเปลี่ยนเกณฑ์เป็น `>= 4` และรายงานเลขจริง |
| DEF-005 | MINOR | `nullability.py` (N01) | `Optional[int]` + `value + 1` ไม่รายงาน | `_deref_symbol()` รับเฉพาะ attribute / subscript / call | เพิ่ม BinOp/Compare ที่มี Name เป็น operand หรือแคบถ้อยคำใน README ให้ตรง |
| DEF-006 | MINOR | `--fix` นอก TTY | พิมพ์ prompt แล้ว `fixes applied: 0` เงียบ ๆ | ไม่เช็ค `sys.stdin.isatty()` | ถ้าไม่ใช่ TTY ให้แจ้ง stderr และไม่ต้อง prompt |
| DEF-007 | MINOR | target `""` | สแกน cwd 52 ไฟล์ | `Path("")` = `.` | ปฏิเสธ target ว่างด้วย exit 4 |
| DEF-008 | MINOR | summary | ไฟล์ parse ไม่ผ่านถูกนับใน "passed" | `unparsable` ไม่ถูกหักออกจาก pass count | แยกคอลัมน์หรือไม่นับเป็น passed |
| DEF-009 | MINOR | stream ของ error | usage error บางตัวออก stdout (`ERROR: Target is not accessible`, `interrupted by user`) แต่ argparse ออก stderr | `reporter.error()` เขียนลง stdout | ส่ง `reporter.error()` ไป stderr |
| DEF-010 | MINOR | `mcp.py` | `ait-mcp --help` เงียบ exit 0 | `main()` เข้า serve loop ทันที | เพิ่ม argparse ขั้นต่ำสำหรับ `--help`/`--version` |
| DEF-011 | MINOR | `ci.yml:126` | `Can't list 'scripts'` แต่ exit 0 | `scripts/` ถูกลบแล้ว แต่ยังอยู่ในคำสั่ง compileall | ตัด `scripts` ออก |

### Repro แบบสั้น

```
# DEF-001
python -c "import sys;sys.path.insert(0,'src');from ait.cli import _encode_like;print(_encode_like('x = 1\n', b'x = 1\n'))"
→ b'\xef\xbb\xbfx = 1\n'

# DEF-002
mkdir d/.ait_cache && : > d/.ait_cache/cache.db
python cli.py d --lf            → collecting 0 files · DEFECT 0 · exit 0 · stderr ว่าง

# DEF-003
# ไฟล์: counter = 0 / global counter / counter = counter + 1 / 4 thread
python cli.py r01.py --no-cache → exit 0 (เปลี่ยนเป็น += แล้วได้ AIT-R01)

# DEF-004
# 5 ชั้น if ซ้อน → ไม่รายงาน · 6 ชั้น → "nests control flow 5 levels deep"

# DEF-005
# def find() -> Optional[int] ... print(value + 1)  → ไม่รายงาน

# DEF-006
python cli.py fixme.py --fix < NUL → "Apply AIT-N01 ...? [y/N/a/q]" + "fixes applied: 0"

# DEF-007
python cli.py "" --no-cache      → target: (ว่าง) · collecting 52 files · exit 1

# DEF-008
python cli.py broken.py          → "0 failed, 1 passed ... 1 unparsable" + exit 3

# DEF-011
python -m compileall -q src cli.py mcp.py scripts   → Can't list 'scripts' · exit 0
```

---

## 5. เอกสารไม่ตรงกับความจริง

| ID | ระดับ | ตำแหน่ง | ปัญหา | หลักฐาน |
|---|---|---|---|---|
| DOC-001 | MINOR | `readme-th.md:218-227` | snippet ใช้รูป `{"mcpServers": {...}}` (แบบ Claude Desktop) แต่ opencode ต้องเป็น `mcp.servers.<name>` + `type: "local"` + `command` เป็น array การเอาไปวางใน opencode เฉย ๆ จะถูกเมิน | รายการที่ใช้ได้จริงใน `C:\Users\xnati\.config\opencode\opencode.jsonc` คือ `mcp.servers.ait.command = [python.exe, mcp.py]` และเรียก tool ผ่าน harness ได้ |
| DOC-002 | MINOR | `docs/demo.txt:1` | เขียนว่า "generated by generate_demo.py" แต่ `scripts/` (ตัว generator) ถูกลบออกจาก repo แล้ว และฝัง path เครื่องตัวเอง `C:\Users\...\Temp\ait-demo-z7f07cz6` | `grep scripts` เจอแค่ `ci.yml:126` และ `.gitignore` |
| DOC-003 | MINOR | `pyproject.toml` vs `--version` | ชื่อ distribution คือ `ait-test` แต่ banner/serverInfo ใช้ `ait-engine` ผู้ใช้ที่ `pip show ait-engine` จะไม่เจอ | `pip show ait-test` → 1.0.0, `ait --version` → `ait-engine 1.0.0` |
| DOC-004 | MINOR | `readme-th.md:275` | เขียนว่า "trace + witness + patch ทุก finding" แต่มีแค่ 5 จาก 11 finding ใน corpus ที่มี patch (A01, A02, H02, M02, R01 ไม่มี) ผู้ใช้จึงคาดว่า `--fix` แก้ได้ทุกอย่าง | MCP `ait_review` บน `ws/mixed`: `patch` เป็น null ใน A01/A02/H02/M02/R01; `--fix --maxfail` บนไฟล์ 3 finding apply ได้ 2 |

---

## 6. ช่องว่างของชุดทดสอบ (จะจับ bug ข้างบนได้ถ้ามี)

| ช่องว่าง | จะจับ |
|---|---|
| ไม่มีเทสต์ยืนยันว่าไฟล์ UTF-8 **ไม่มี BOM** ต้องไม่มี BOM หลัง `--fix` (มีแต่เคส BOM มีอยู่แล้วต้องคงอยู่) | DEF-001 |
| ไม่มีเทสต์ `--lf` ตอน index ว่าง/ถูกลบ (มีแต่เคส cache เปิดไม่ได้) | DEF-002, DEF-003 (เชิงพฤติกรรม) |
| ไม่มีเทสต์ RMW รูปแบบ Assign (`x = x + 1`) | DEF-003 |
| ไม่มีเทสต์ขอบเขต H02 (5 ชั้น vs 6 ชั้น) | DEF-004 |
| ไม่มีเทสต์ `--fix` นอก TTY | DEF-006 |
| ไม่มีเทสต์ target ว่าง | DEF-007 |

ข้อเสนอ: เพิ่ม 6 เทสต์ใน `tests/test_cli_integration.py` (ชื่อเดิม `EncodingTests` / `CacheTests` / `RuleBoundaryTests`) จะทำให้ตัวเลข 250 เปลี่ยนเป็น ~256 ต้องอัปเดต README และ badge พร้อมกัน

> ทำแล้วในรอบที่ 2: เพิ่ม **18** เทสต์ (ไม่ใช่ 6) ครอบทั้ง 6 ช่องว่างข้างบน → `unittest` 268 tests (§10)

---

## 7. สิ่งที่ยืนยันแล้วว่าถูกต้อง

- exit code ตรงสัญญา: 0 clean · 1 DEFECT · 2 interrupt · 3 parse · 4 usage (ทดสอบครบทุกตัว รวม SIGINT จริง)
- self-scan 0/0/0 ทั้ง Windows และ WSL, รันได้ด้วย `python -S` (ไม่มี third-party จริง)
- 13 กฎยิงได้ครบทั้ง 13 ID; ทุก finding มี evidence + causation + witness (patch มีเฉพาะบาง rule ตาม DOC-004)
- MCP: schema ปิด, error code ตรงตาม JSON-RPC (`-32602`, `-32601`, `-32700`, `-32600`), `isError:false` แม้เจอ DEFECT, `structuredContent` + text mirror, shutdown สะอาด
- cache: key อิง digest จริง (แก้ไฟล์แล้ว findings อัปเดตทันที), WAL, `--no-cache` ไม่ทิ้ง artefact, ไม่มี `.ait-tmp` ค้าง
- encoding: cp874 / BOM / CRLF อ่านได้และเขียนกลับคง codec + line ending + ภาษาไทย (DEF-001 ที่เติม BOM แก้แล้วในรอบที่ 2)
- `--fix` มี backup ที่เป็น byte เดิมก่อน patch, rescan ยืนยันก่อนจบ, และ apply หลาย patch ในไฟล์เดียวได้โดยไม่ทับกัน (ทดสอบ 2 patch) ส่วนกลไกปฏิเสธ patch ที่จะทำให้เกิด finding ใหม่มีอยู่ในโค้ด (`cli.py:363-366`) แต่ยังไม่มีเคสจริงมากระตุ้น
- packaging: editable install และ wheel clean-room ใช้งานได้, metadata ไม่มี `Requires-Dist`
- ตัวเลขใน README (268 tests, 23 ไฟล์, 7,080 บรรทัด, 13 rules, 15 job runs, perf table) ตรงกับของจริงหลังอัปเดตรอบที่ 2

---

## 8. ข้อจำกัด

- ทดสอบบน Windows 11 + WSL2 เท่านั้น ไม่ได้รันบน macOS หรือ GitHub runner จริง (CI badge จึงยังเป็นการอ้างอิง ไม่ใช่ผลรันล่าสุด)
- exit code 2 บน Windows ทดสอบด้วย SIGINT ผ่าน `timeout -s INT` ใน WSL ส่วน Ctrl+C จริงบน console Windows ให้ `STATUS_CONTROL_C_EXIT` (พฤติกรรมของ Windows เอง ไม่ใช่บั๊กของ engine)
- ไม่ได้ทดสอบ `--fix` กับ patch ที่จะทำให้เกิด finding ใหม่ (ต้องสร้างเคสเทียมที่ rule ยอมรับ patch แต่ผลลัพธ์แย่ลง)
- ไม่ได้ทดสอบ concurrence ของ MCP server (หลาย request พร้อมกัน)
- ไม่ได้ทดสอบไฟล์ขนาดใหญ่ระดับ GB หรือ symlink loop

---

## 9. Checklist ก่อนปล่อย v1.0.0

- [x] แก้ `DEF-001` + เพิ่มเทสต์ BOM-less round-trip
- [x] แก้ `DEF-002` + เพิ่มเทสต์ `--lf` ตอน index ว่าง/ถูกลบ
- [x] แก้ `DEF-003` + เพิ่มเทสต์ RMW รูปแบบ Assign
- [x] แก้ `DEF-004`, `DEF-005` (หรือปรับถ้อยคำเอกสารให้ตรงกับพฤติกรรม)
- [x] แก้ `DEF-006` ถึง `DEF-011`
- [x] ปรับ `DOC-001` (เพิ่มตัวอย่าง config ของ opencode), `DOC-002`, `DOC-004`
- [x] อัปเดตจำนวนเทสต์ใน README/badge ถ้าเพิ่มเทสต์
- [x] รัน `unittest` + self-scan + `ruff` + `bandit` + wheel ซ้ำหลังแก้

---

## 10. ผลตรวจซ้ำหลังแก้ (รอบที่ 2)

แก้ทั้ง 11 ข้อบกพร่องและเอกสาร 4 จุด แล้วรัน repro เดิมซ้ำทุกข้อบนโค้ดที่แก้ ไม่มีข้อไหนปิดแบบ "เชื่อว่าหาย" โดยไม่มีหลักฐาน

### 10.1 สถานะรายข้อ

| ID | สถานะ | จุดที่แก้ | หลักฐานก่อน → หลัง |
|---|---|---|---|
| DEF-001 | ✅ FIXED | `cli._encode_like` เลือก `utf-8-sig` เฉพาะเมื่อไฟล์มี BOM จริง | `_encode_like('x = 1\n', b'x = 1\n')` เดิมได้ `b'\xef\xbb\xbfx = 1\n'` → ตอนนี้ได้ `b'x = 1\n'`; ไฟล์ที่มี BOM ยังได้ `first3=efbbbf`, ไฟล์ธรรมดา `first3=66726f` ผ่าน `--fix` จริงบน pty |
| DEF-002 | ✅ FIXED | `engine.last_failed_selection` ใช้ร่วมกันทั้ง CLI และ `Engine.scan`; `indexed == 0` หรือ cache ใช้ไม่ได้ = สแกนทุกไฟล์ + stderr | cache ถูกลบ / 0 ไบต์ / เขียนขยะ / ไม่มีโฟลเดอร์: เดิม `collecting 0 files` exit 0 ไม่มี stderr → ตอนนี้ `collecting 2 files` exit 1 + `ait: --lf found no previous run in the cache; scanning every file`; เคสปกติยัง narrow เหลือ 1 ไฟล์ |
| DEF-003 | ✅ FIXED | `WriteRecord.reads_target` + `_reads_target()` ใน call graph; `race_hazards._is_read_modify_write` รับ `assign` ที่อ่าน target เดิม | `self.total = self.total + 1` เดิมเห็นแค่ Z03 → ตอนนี้ R01 DEFECT (symbol `self.total`); `TOTAL = TOTAL + 1` ก็ยิง R01; `self.total = compute()` ไม่ยิง (ไม่มี RMW) |
| DEF-004 | ✅ FIXED | `hygiene.check_deep_nesting` seed depth 1 เมื่อ statement แรกเป็น control node | 5 ชั้น if เดิมเงียบ → ตอนนี้ `` `f` nests control flow 5 levels deep ``; 4 ชั้นยังเงียบ; ตัว engine เองโผล่ H02 2 จุดจึงรีแฟกเตอร์ `_parameter_confidence()` และ `_is_lock_with()`/`_nested_conditions()` จน self-scan กลับเป็น 0/0/0 |
| DEF-005 | ✅ FIXED | `nullability._deref_symbol` รับ operand ของ `BinOp` และ ordering `Compare`; `is`/`==` ไม่ถือเป็น dereference | `return value + 1` และ `return value < 3` เดิมเงียบ → ตอนนี้ N01 ทั้งคู่; `if value is not None and value < 3` และ `return value is None` ยังสะอาด |
| DEF-006 | ✅ FIXED | `cli._stdin_is_interactive()` เพิ่ม `GetConsoleMode` สำหรับ Windows (NUL/DEVNULL เป็น char device ที่ `isatty()` คืน True) | `--fix` ด้วย `stdin=DEVNULL` / `NUL`: เดิมพิมพ์ prompt ค้างแล้วสรุปเงียบ ๆ → ตอนนี้ไม่พิมพ์ prompt, stderr `ERROR: --fix requires an interactive terminal; nothing was modified.`, ไฟล์ไม่เปลี่ยน; pipe (`echo y`) ยังถูกปฏิเสธเหมือนเดิม |
| DEF-007 | ✅ FIXED | `Engine.discover` ปฏิเสธ target ที่ว่าง/มีแต่ช่องว่าง | `ait ""` เดิมสแกน cwd 52 ไฟล์ → ตอนนี้ exit 4, stdout ว่าง, stderr `ERROR: Target is empty; pass a file or directory path` |
| DEF-008 | ✅ FIXED | `ScanResult.file_outcomes` ใช้ `files_processed` ตรง ๆ ไม่ fallback ไป `files_total` | โฟลเดอร์ที่มีแต่ไฟล์ parse ไม่ผ่าน: เดิมนับเป็น passed → ตอนนี้ `0 failed, 0 passed in …, 1 unparsable` exit 3 |
| DEF-009 | ✅ FIXED | `Reporter` มี `error_stream` (default `sys.stderr`); `error()` เขียนที่นั่น | `ait missing-path` เดิม `ERROR:` ออก stdout → ตอนนี้ stdout ว่าง, stderr มี `ERROR: Target is not accessible: …` (ทดสอบยืนยันแบบ assert เท่ากับ string ว่าง) |
| DEF-010 | ✅ FIXED | `mcp.main` ใช้ argparse (`--help`, `--version`), ไม่มี argument จึงเข้า serve loop | `ait-mcp --help` exit 0 + `usage: ait-mcp`, `--version` exit 0 + `ait-engine-mcp 1.0.0`, flag แปลก exit 2; โหมด serve เดิมยังผ่านเทสต์ protocol ทั้งชุด |
| DEF-011 | ✅ FIXED | `.github/workflows/ci.yml` ตัด `scripts` ออกจาก `compileall` | `python -m compileall -q src cli.py mcp.py` exit 0 และไม่มีข้อความ `Can't list 'scripts'` |

### 10.2 เอกสาร

| ID | สถานะ | สิ่งที่แก้ |
|---|---|---|
| DOC-001 | ✅ | `readme-th.md` เพิ่ม snippet แบบ opencode (`mcp.servers.<name>` + `type: "local"` + `command` เป็น array) ควบคู่กับ `mcpServers` ของ Claude Desktop และระบุ `ait-mcp --help/--version` |
| DOC-002 | ✅ | `docs/demo.txt` เปลี่ยนหัวเป็น "Real transcript captured from a temporary workspace (paths anonymised)" และแทน `rootdir` ที่ฝัง path เครื่องด้วย `<workspace>` |
| DOC-003 | ✅ | ระบุชัดว่า distribution คือ `ait-test` แต่ engine/`--version`/MCP `serverInfo` คือ `ait-engine 1.0.0` (คนละชั้น ไม่ใช่สองแพ็กเกจ) |
| DOC-004 | ✅ | เปลี่ยน "patch ทุก finding" เป็น "patch เฉพาะจุดที่แก้ได้แบบ deterministic (A01/A02/H02/M02/R01 ไม่มี patch)" |

### 10.3 เทสต์ที่เพิ่ม (18 ตัว → 250 เป็น 268)

| ไฟล์ | เทสต์ |
|---|---|
| `tests/test_cli_integration.py` (7) | unparsable ไม่ถูกนับ passed · target ว่าง exit 4 · usage error ออก stderr เท่านั้น · `--lf` ตอน index ว่าง · `--fix` กับ DEVNULL ไม่ prompt · UTF-8 ไม่มี BOM ต้องไม่ถูกเติม BOM (unit) · `_apply_single` บนไฟล์ธรรมดาไม่เติม BOM (round-trip) |
| `tests/test_rules_race.py` (3) | `self.x = self.x + 1` เป็น DEFECT · `TOTAL = TOTAL + 1` เป็น R01 · `self.x = compute()` ไม่ใช่ race |
| `tests/test_rules_invariants.py` (5) | N01 กับ `+` · N01 กับ `<` · `is None` ไม่ใช่ dereference · H02 5 ชั้นรายงาน "5 levels deep" · H02 4 ชั้นเงียบ |
| `tests/test_mcp_protocol.py` (3) | `--help` exit 0 · `--version` exit 0 · flag แปลก exit 2 |

### 10.4 Quality gates หลังแก้

| Gate | ผล |
|---|---|
| `python -m unittest discover -s tests` | **268 tests OK** บน 8 interpreter (3.10.21, 3.11.16, 3.12.10, 3.12.14, 3.13.15, 3.14.4 WSL, 3.14.7, 3.15.0rc2) |
| 3.15 + `-W error::DeprecationWarning` | 268 OK |
| self-scan `python cli.py src --no-cache` | exit 0 · DEFECT 0 · WARNING 0 · ADVICE 0 (23/23 ไฟล์) |
| `python -S cli.py src` | exit 0 · 0/0/0 |
| `ruff check .` (0.17.2) | 0 findings |
| `bandit -c pyproject.toml -r src cli.py mcp.py -ll` | exit 0 |
| `compileall` (`src`, `cli.py`, `mcp.py`, `tests`) | exit 0 |
| `uv build --wheel` + ติดตั้งใน venv สะอาด (`--no-index --no-deps`) | `ait-test 1.0.0`, `requires: None`, `ait --version` → `ait-engine 1.0.0`, `ait-mcp --version` → `ait-engine-mcp 1.0.0`, สแกน `src` 23 ไฟล์ 0/0/0 |
| `tests/fixtures` corpus | exit 1 · DEFECT 11 · WARNING 4 · ADVICE 1 (ไม่เปลี่ยนจากรอบแรก) |

### 10.5 ตัวเลขที่วัดใหม่ (ใช้ใน README แล้ว)

| สถานการณ์ | 3.12.10 (Windows) | 3.14.4 (WSL2 Ubuntu) | 3.15.0rc2 (Windows) |
|---|---|---|---|
| สแกน `src/` 23 ไฟล์ (cold) | 642 ms | 725 ms | 510 ms |
| สแกนซ้ำ (warm cache) | 12.3 ms | 270 ms | 9.9 ms |
| Test suite (268 tests) | 6.93 s | 17.33 s | 6.77 s |

`src/ait` = 23 ไฟล์ **7,080 บรรทัด** (เดิม 6,879 — เพิ่มจากโค้ดที่แก้) · README/badge อัปเดตเป็น 268 tests แล้ว

### 10.6 ข้อสังเกตที่ยังเหลือ

- `--fix` ยังตั้งใจไม่รับคำตอบจาก pipe (ต้องมี console จริง) — ตรงกับเอกสาร และตอนนี้ NUL/DEVNULL ก็ถูกปฏิเสธเหมือน pipe
- กฎ R01 ยังรายงาน global RMW ได้เฉพาะเมื่อมี spawn site ที่ resolve ได้ (พฤติกรรมเดิม ไม่ได้ขยายในรอบนี้)
- DOC-001 ยังคงเก็บ snippet แบบ `mcpServers` ไว้เพราะใช้ได้กับ Claude Desktop; opencode ต้องใช้บล็อกที่เพิ่มเข้ามา

---

## 11. หลังปล่อย v1.0.0

ปล่อยเมื่อ 2026-10-24 และตรวจย้อนจาก GitHub API โดยตรง

| รายการ | ผล |
|---|---|
| commit | `e2bb2d4` บน `main` |
| CI run แรกหลัง push | 15/15 jobs success (12 test environments + `package` + `lint` + `security`) ที่ https://github.com/xTanTHaix/AiT-Test/actions/runs/40085047779 |
| tag | `v1.0.0` (annotated) ชี้ `e2bb2d4` |
| release | https://github.com/xTanTHaix/AiT-Test/releases/tag/v1.0.0 พร้อม asset `ait_test-1.0.0-py3-none-any.whl` (91,113 ไบต์) |
| topics | 14 หัวข้อ |
| license ที่ GitHub ตรวจพบ | MIT (จาก `LICENSE`) |

สิ่งที่เปลี่ยนหลังรอบ QA:

- เพิ่ม `README.md` ฉบับอังกฤษ แปลจาก `readme-th.md` โครงเดียวกัน (14 หัวข้อ, 14 `<details>`, 8 ตาราง) พร้อม language switch ทั้งสองไฟล์
- `pyproject.toml`: `readme = "README.md"`, `license = "MIT"` (SPDX string), `setuptools>=77`
- `.gitattributes` บังคับ `eol=lf` เพื่อให้ line number ในผลสแกนตรงกันทุกแพลตฟอร์ม
- ตรวจ wheel ซ้ำใน venv สะอาด: `License-Expression: MIT`, มี `licenses/LICENSE`, ไม่มี `Requires-Dist`, `ait` / `ait-mcp` ทำงานนอก source tree

ข้อจำกัดข้อแรกใน §8 (ยังไม่ได้รันบน CI จริง) ปิดแล้วด้วย run ข้างต้น

---

## 12. CodeQL หลังเปิดใช้

เปิด `.github/workflows/codeql.yml` แบบ advanced setup เพราะ default setup ของ repo ยังเป็น `not-configured`
ใช้ชุด `security-and-quality` และ pin `github/codeql-action` v4.39.1 ที่ commit `9b3254085494b55a0f7c4c2b3d2d52cc712ce370`

| รอบ | commit | ผล |
|---|---|---|
| 1 | `21782e8` | analyze success, 9 alerts, 172 rules |
| 2 | `ff6be3f` | analyze success, 0 alerts |

แก้ทั้ง 9 รายการ ไม่ได้ dismiss สักข้อ

| alert | ไฟล์ | สิ่งที่แก้ |
|---|---|---|
| `py/empty-except` ×3 | `cache.py` 2 จุด, `cli.py` 1 จุด | เปลี่ยนเป็น `contextlib.suppress(...)` |
| `py/import-and-import-from` | `tests/test_cli_integration.py` | รวมเป็น `from unittest import TestCase, main, mock` |
| `py/unused-import` | `tests/test_cache.py` | เอา side-effect import ออก แล้วเขียน anchor `sys.path` ตรง ๆ |
| `py/unused-local-variable` ×2 | `engine.py`, `flow/call_graph.py` | ตัด binding ที่ไม่ถูกใช้ |
| `py/catch-base-exception`, `py/modification-of-default-value` | `tests/fixtures` | กันโฟลเดอร์ออกด้วย `codeql-config.yml` เพราะเป็นโค้ดที่ตั้งใจให้พัง |

หลังแก้: CI 15/15 และ CodeQL success ที่ `ff6be3f`
(https://github.com/xTanTHaix/AiT-Test/actions/runs/40086576026 และ https://github.com/xTanTHaix/AiT-Test/actions/runs/40086575990)
badge ที่ README เป็นสถานะสดของ workflow นี้ ไม่ใช่ตัวเลขที่เขียนตายตัว

ยังไม่เปิด `dependency-review` เพราะแพ็กเกจประกาศ dependency เป็นศูนย์ และไม่ได้ใส่ badge ของ OSV
เพราะ OSV ไม่มี badge ทางการ ส่วน shield แบบ "0 issues" จะค้างอยู่ที่ 0 แม้มี advisory ใหม่
สถานะ OSV จึงดูจาก job `security` ใน CI ตามเดิม

