<h1 align="center">🛡️ AiT-test</h1>

<h3 align="center">สแตติกอนาไลซิส Python ที่ไล่ถึงต้นตอ ไม่ใช่แค่ชี้บรรทัด</h3>

<p align="center"><b>Causation Trace · Repro Witness · Verified Patch · Zero Dependencies</b></p>

<img width="100%" alt="AiT-Test: zero runtime dependencies, causation trace, repro witness and a verified patch where a deterministic fix exists, 13 evidence-based rules" src="docs/assets/AiT.gif" />

<p align="center"><i>ภาพรวมสี่เสาหลักของ engine · ตัวเลขบนภาพมาจากเครื่องของผู้เขียน ตารางที่ reproduce ได้อยู่ที่ <a href="#metrics">ผลวัดจริง</a></i></p>

---

<p align="center"><a href="#"><img alt="Python 3.10 to 3.15" src="https://img.shields.io/badge/python-3.10%20%E2%86%92%203.15-3776AB?style=flat-square&logo=python&logoColor=white"></a> <a href="#"><img alt="Version v1.0.0" src="https://img.shields.io/badge/version-v1.0.0-6366F1?style=flat-square&logo=semver&logoColor=white"></a> <a href="#tests"><img alt="268 tests passing" src="https://img.shields.io/badge/tests-268%20passing-059669?style=flat-square&logo=checkmarx&logoColor=white"></a> <a href="#ci"><img alt="CI: 15 job runs" src="https://img.shields.io/badge/CI-15%20job%20runs-2088FF?style=flat-square&logo=githubactions&logoColor=white"></a> <a href="#"><img alt="Zero runtime dependencies" src="https://img.shields.io/badge/runtime%20dependencies-0-22C55E?style=flat-square&logo=python&logoColor=white"></a> <a href="#rules"><img alt="13 rules" src="https://img.shields.io/badge/rules-13-0284C7?style=flat-square&logo=shieldsdotio&logoColor=white"></a> <a href="#"><img alt="Windows and Linux" src="https://img.shields.io/badge/platforms-Windows%20%7C%20Linux-0284C7?style=flat-square&logo=linux&logoColor=white"></a> <a href="#credits"><img alt="Author xTanTHaix" src="https://img.shields.io/badge/author-xTanTHaix-8B5CF6?style=flat-square&logo=github&logoColor=white"></a> <a href="LICENSE"><img alt="MIT License" src="https://img.shields.io/badge/license-MIT-F59E0B?style=flat-square&logo=googledocs&logoColor=white"></a></p>

<p align="center"><a href="docs/demo.txt">🎬 ผลรันจริง</a> • <a href="#rules">🧭 กฎ 13 ข้อ</a> • <a href="#cli">🖥️ CLI</a> • <a href="#mcp">🤖 MCP</a> • <a href="#tests">🧪 การทดสอบ</a> • <a href="#ci">🔄 CI</a> • <a href="#credits">🏅 เครดิต</a></p>

<p align="center"><a href="README.md">English</a> · <a href="readme-th.md">ไทย</a></p>

> [!IMPORTANT]
> ทำงานด้วย AST ของ standard library ล้วน: **ไม่มี dependency ตอนรัน และไม่ต้องต่อเน็ต**
> witness ถูกสร้างเป็นข้อความและตรวจ syntax เท่านั้น ไม่มีการ execute โค้ดที่ตรวจจับได้

---

## 🎯 ปัญหาที่มันแก้

linter ทั่วไปบอกตำแหน่ง แต่ไม่บอกเส้นทางที่ทำให้ค่านั้นเป็น `None`
คนอ่านจึงต้องเปิดไฟล์ ไล่ย้อนขึ้นไปหาต้นทาง แล้วเดาเองว่า patch ที่คิดไว้จะไปพังอย่างอื่นหรือเปล่า

| ⚠️ คำถามที่ค้างอยู่ | ⚡ สิ่งที่ AiT-test ตอบในผลลัพธ์เดียว |
| :--- | :--- |
| ค่า `None` นี้มาจากไหน | **Causation trace**: ทุก step มี `file:line:col` ตั้งแต่ producer ถึงจุดที่พัง |
| จะ reproduce ได้ยังไง | **Repro witness**: โค้ดสั้นที่สุดที่ทำให้เกิดอาการ พร้อมธง `safe_to_execute` |
| แก้แล้วจะพังอย่างอื่นไหม | **Suggested fix**: unified diff ที่ผ่าน `ast.parse` และถูกสแกนซ้ำว่าไม่มี finding ใหม่ |
| ไฟล์ไหนควรแก้ก่อน | **DEFECT ต้องมีหลักฐาน**: ขาดหลักฐาน concurrency/type จะลดเป็น WARNING แทนการเดา |

---

## ⚡ เริ่มใช้

ต้องมี Python 3.10 ขึ้นไป ไม่ต้องติดตั้งอะไรเพิ่ม:

```bash
python cli.py src
```

หรือติดตั้งเป็นคำสั่ง `ait` / `ait-mcp` ก็ได้ (ยังไม่มี dependency อยู่ดี):

```bash
pip install -e .
ait src
```

ชื่อ distribution ที่ใช้ติดตั้งคือ `ait-test` (`pip show ait-test`) ส่วนชื่อ engine ที่ `--version` และ MCP `serverInfo` รายงานคือ `ait-engine 1.0.0` เป็นโปรเจกต์เดียวกันคนละชั้น ไม่ใช่สองแพ็กเกจ

<img width="100%" alt="AiT-test ตรวจจับ NoneType ที่ไม่ได้ป้องกัน แล้วยืนยันว่าหายไปหลังใส่ guard" src="docs/demo.gif" />

<p align="center"><i>รันจริงบน <code>demo.py</code>: ตรวจพบ N01 พร้อม causation trace, witness และ diff ที่ผ่านการตรวจซ้ำ</i></p>

<details>
<summary><b>🎬 คลิกเพื่อขยาย: ข้อความผลลัพธ์เต็มจาก GIF ด้านบน</b></summary>

ข้อความเต็มอยู่ใน [`docs/demo.txt`](docs/demo.txt) และ GIF ด้านบน generate จากการรันจริง

```text
$ python cli.py demo.py

================== [AIT-N01] Unshielded NoneType Dereference ===================
File: demo.py:10:10
Severity: DEFECT (Confidence: 0.90)
Function: render()
Symbol: value

  `value` can be None at this point but is dereferenced without a guard

--- Causation Trace ---
  Step 1 | demo.py:9:12
         | `lookup()` is declared to return `Optional[str]`, so the result can be None
  Step 2 | demo.py:9:4
         | `value` receives the return value of `lookup()`
  Step 3 | demo.py:10:10
         | `value` is dereferenced here without an `is None` guard

--- Suggested Fix ---
  --- a/demo.py
  +++ b/demo.py
  @@ -10,1 +10,3 @@
  +    if value is None:
  +        raise RuntimeError(f"value is unexpectedly None")
       print(value.upper())

========================= 1 failed, 0 passed in 2.59ms =========================
  DEFECT 1 · WARNING 0 · ADVICE 0
  exit code: 1
```

โค้ดที่ใช้สาธิต:

```python
from typing import Optional


def lookup(key: str) -> Optional[str]:
    return None


def render() -> None:
    value = lookup("alpha")
    print(value.upper())
```

</details>

---

<a id="rules" name="rules"></a>
## 🧭 กฎ 13 ข้อ

<details>
<summary><b>📋 คลิกเพื่อขยาย: ตารางกฎทั้ง 13 ข้อ + เงื่อนไขที่ต้องมีจริง</b></summary>

| ID | ระดับ | จับอะไร | เงื่อนไขที่ต้องมีจริง |
|---|---|---|---|
| `AIT-Z01` | DEFECT | `Popen(..., stdout=PIPE)` แล้ว `wait()` โดยไม่ drain ท่อ | มี pipe + มีการรอ child โดยไม่มี `communicate()`/`read()` มาก่อน |
| `AIT-Z02` | DEFECT | ทิ้ง handle ของ child โดยไม่ `wait`/`poll`/คืน ownership | resolve ตัวแปรได้ว่าเป็น process handle |
| `AIT-Z03` | WARNING | spawn process/thread ที่ module scope โดยไม่มี `if __name__ == "__main__":` | call อยู่ชั้น module จริง |
| `AIT-R01` | DEFECT / WARNING | read-modify-write บน state ร่วมที่ไม่มี lock ครอบ | ต้องมีหลักฐาน concurrency (spawn site, `async def`, หรือ lock ในคลาส) ไม่งั้นไม่รายงาน |
| `AIT-R02` | WARNING | ตรวจ condition นอก lock แล้วตรวจซ้ำใน lock | ต้องมี lock region จริงและ condition เดียวกัน |
| `AIT-A01` | DEFECT / WARNING | blocking call ใน `async def` | `time.sleep`/network/process = DEFECT, file I/O = WARNING; ถ้า offload ผ่าน `to_thread`/executor จะไม่รายงาน |
| `AIT-A02` | WARNING | `create_task()` แล้วไม่มีใครถือ reference | ต้องไม่ถูกเก็บใส่ตัวแปร, ไม่ถูก await, ไม่มี done callback |
| `AIT-A03` | DEFECT | เรียก coroutine แล้วไม่ await | ต้อง resolve ได้ว่า callee เป็น coroutine ในไฟล์เดียวกันหรือ API ที่รู้จัก |
| `AIT-M01` | DEFECT | แก้ parameter ของผู้เรียกในที่ (`append`, `pop`, `update`, ...) | ต้องเป็นการเขียนบนชื่อ parameter โดยตรง |
| `AIT-M02` | DEFECT | default argument เป็น `[]`, `{}`, `set()`, ... | literal/constructor ที่ mutable จริง |
| `AIT-N01` | DEFECT | dereference ค่าที่อาจเป็น `None` โดยไม่มี guard | ต้องมีหลักฐานว่า optional (annotation, `None` literal, producer ที่คืน optional, `dict.get`) |
| `AIT-H01` | WARNING | `except:` ที่กลืนทุกอย่าง | `except: raise` (re-raise เปล่า) ไม่รายงาน เพราะไม่ได้กลืนอะไร |
| `AIT-H02` | ADVICE | ควบคุมซ้อนกันเกิน 4 ชั้น | นับเฉพาะ control node จริง ไม่ข้าม scope |

</details>

> [!NOTE]
> **นโยบายความรุนแรง:** DEFECT ต้องมีหลักฐานประกอบ ถ้าขาดหลักฐานเรื่อง concurrency หรือ type
> จะลดเป็น WARNING แทนการเดา เพราะ finding ปลอมหนึ่งอันทำให้คนเลิกอ่านรายงานทั้งหมด

---

<a id="cli" name="cli"></a>
## 🖥️ CLI

```text
python cli.py [target] [options]
```

| Flag | ผล |
|---|---|
| `-v, --verbose` | แสดงรายละเอียดต่อไฟล์ |
| `-q, --quiet` | แสดงแค่สรุป |
| `-x, --exitfirst` | หยุดที่ไฟล์แรกที่มี DEFECT |
| `--lf, --last-failed` | สแกนเฉพาะไฟล์ที่ fail รอบก่อน (ต้องใช้ cache) |
| `--fix` | แสดง diff แล้วถามทีละอัน (ต้องมี TTY) |
| `--no-cache` | ข้าม cache ทั้งอ่านและเขียน |
| `--maxfail N` | แสดง failure block ไม่เกิน N อัน |
| `--no-color` | ปิด ANSI (หรือตั้ง `NO_COLOR=1`) |
| `--version` | แสดงเวอร์ชัน |

**Exit code:** `0` สะอาด · `1` มี DEFECT · `2` ถูก interrupt · `3` parse/internal error · `4` ใช้งานผิด

> [!TIP]
> ถ้า `--lf` เจอ cache ที่เปิดไม่ได้ ไม่มี index หรือยังไม่เคยรันมาก่อน จะสแกนทุกไฟล์พร้อมแจ้งทาง stderr
> แทนที่จะวิเคราะห์ 0 ไฟล์แล้วรายงานว่า "สะอาด" ทั้งที่ไม่ได้อ่านอะไรเลย

<details>
<summary><b>🔧 คลิกเพื่อขยาย: <code>--fix</code> ทำงานอย่างไร (7 ขั้นตอน)</b></summary>

1. patch ทุกอันถูก `ast.parse` ซ้ำก่อนถูกเสนอ
2. ถามทีละอัน (`y` / `N` / `a` ทั้งหมด / `q` ออก)
3. ก่อนเขียน: copy ต้นฉบับไป `.ait_backup/<timestamp>-<pid>/`
4. เขียนไฟล์ชั่วคราวแล้ว `os.replace` (atomic) โดยเขียนเป็น bytes ไม่แปลง line ending
5. เขียนกลับด้วย codec เดิมของไฟล์ (`utf-8-sig` คง BOM ไว้, ไฟล์ UTF-8 ที่ไม่มี BOM จะไม่ถูกเติม BOM, ไฟล์ `cp874` ยังเป็น `cp874`)
   ถ้า patch มีอักขระที่ codec เดิม encode ไม่ได้ จะปฏิเสธการเขียนแทนที่จะเปลี่ยนไฟล์เป็น UTF-8 เงียบ ๆ
6. สแกนไฟล์นั้นใหม่ ถ้ามี finding ใหม่โผล่ → rollback ทันที
7. จบแล้วสแกนทั้ง target ใหม่เพื่อยืนยันสถานะสุดท้าย; ถ้าโดน Ctrl-C ระหว่างทางจะพิมพ์จำนวนที่แก้ไปแล้วกับที่เก็บ backup ไว้

</details>

<details>
<summary><b>🔇 คลิกเพื่อขยาย: การปิด finding ที่ตั้งใจ + <code>.aitignore</code></b></summary>

Inline directive ใช้ได้ทั้งแบบมี prefix และไม่มี (คนอ่านมักจำเลขกฎจากผลลัพธ์):

```python
value.upper()  # ait: disable=AIT-N01
```

```python
except:  # ait: disable=H01
    pass
```

```python
except:  # ait: disable=ALL
    pass
```

finding ที่ถูกปิดจะไม่ถูกพิมพ์ แต่ยังนับรวมในบรรทัดสรุปเป็น `N suppressed` เพื่อไม่ให้ของที่ถูกกลบหายไปเงียบ ๆ

หรือใส่ glob ไว้ใน `.aitignore` ที่ root ของโปรเจกต์:

```text
legacy_*.py
build/
```

</details>

---

<a id="mcp" name="mcp"></a>
## 🤖 MCP

`mcp.py` เป็น JSON-RPC 2.0 over stdio ใช้กับ MCP client ได้ทันที stdout มีแต่ frame ของ protocol ส่วน log ไปที่ stderr

```json
{
  "mcpServers": {
    "ait": {
      "command": "python",
      "args": ["L:\\path\\to\\AiT-Test\\mcp.py"]
    }
  }
}
```

opencode (`opencode.jsonc`) ใช้ `mcp.servers.<name>` และ `command` เป็น array ไม่ใช่รูป `mcpServers` ด้านบน:

```jsonc
{
  "mcp": {
    "servers": {
      "ait": {
        "type": "local",
        "command": ["python", "L:\\path\\to\\AiT-Test\\mcp.py"]
      }
    }
  }
}
```

`ait-mcp --help` กับ `ait-mcp --version` ตรวจการติดตั้งได้โดยไม่เข้า serve loop

| Tool | ใช้ทำอะไร |
|---|---|
| `ait_review` | สแกนไฟล์/โฟลเดอร์ คืน findings พร้อม causation, witness, diff |
| `ait_slice_context` | คืน source + variable table + calls + callers ของ symbol เดียว (ประหยัด token) |
| `ait_verify_fix` | เทียบ candidate code กับ baseline ว่าอะไรหาย อะไรโผล่ใหม่ |

<details>
<summary><b>📐 คลิกเพื่อขยาย: สัญญาของ transport และ argument ที่แต่ละ tool รับ</b></summary>

| Tool | Arguments |
|---|---|
| `ait_review` | `path` (required), `severity_min` = `ADVICE`/`WARNING`/`DEFECT`, `max_findings` = 1–500 (default 50), `exitfirst`, `use_cache` |
| `ait_slice_context` | `file_path`, `symbol_name` (required), `depth` = 0–3 (default 1) |
| `ait_verify_fix` | `file_path`, `candidate_code` (required) |

- `isError: false` เมื่อ tool ทำงานสำเร็จ **แม้จะเจอ DEFECT** เพราะ finding คือผลลัพธ์ที่ใช้ได้ ไม่ใช่ความล้มเหลวของเครื่องมือ
  (`isError: true` สงวนไว้ให้กรณี tool ทำงานไม่ได้จริง เช่น candidate parse ไม่ผ่าน)
- argument ที่ schema ไม่ได้ประกาศ, ชนิดผิด, หรือค่านอกช่วง จะได้ `-32602` ไม่ใช่ถูกเมินหรือ clamp เงียบ ๆ
- exception ที่ไม่คาดคิด (เช่น `RecursionError`) ถูกตอบกลับเป็น `-32603` และ process ยังรับ request ต่อไปได้
- `shutdown` และ `notifications/exit` หยุด serve loop; `initialize` ไม่ประกาศ capability `logging` เพราะไม่ได้ใช้
- `structuredContent` มี object เดียว ส่วน `content[0].text` เป็น compact JSON mirror (ไม่ indent) เพื่อไม่ให้ payload โตเป็นสองเท่า

</details>

<details>
<summary><b>🧪 คลิกเพื่อขยาย: ตัวอย่างการเรียก <code>ait_verify_fix</code></b></summary>

```json
{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{
  "name":"ait_verify_fix",
  "arguments":{
    "file_path":"demo.py",
    "candidate_code":"from typing import Optional\n\ndef lookup(k: str) -> Optional[str]:\n    return None\n\ndef render() -> None:\n    value = lookup('a')\n    if value is None:\n        return\n    print(value.upper())\n"
  }
}}
```

`ait_verify_fix` ตรวจ invariant ระดับ AST ซ้ำ ไม่ได้พิสูจน์เชิงคณิตศาสตร์
มันยืนยันได้แค่ว่า "กฎทั้ง 13 ข้อไม่พบอะไรในโค้ดใหม่" และไม่ได้แปลว่าโปรแกรมถูกต้องทุกกรณี

</details>

---

## 📊 ตารางความสามารถและหลักฐาน

**0 runtime deps · offline · 13 กฎ · trace + witness ทุก finding · patch เฉพาะจุดที่แก้ได้แบบ deterministic (A01/A02/H02/M02/R01 ไม่มี patch) · cache แบบ digest-aware**

<details>
<summary><b>📋 คลิกเพื่อขยาย: ตาราง 9 มิติ + หลักฐานในโปรเจกต์</b></summary>

| มิติ | สถานะ | หลักฐานในโปรเจกต์ |
| :--- | :---: | :--- |
| Runtime dependencies | ✅ 0 | import ทั้งหมดมาจาก standard library; `python -S` ก็สแกนได้ |
| ทำงาน offline | ✅ ใช่ | ไม่มี HTTP client, ไม่มี telemetry |
| Causation trace | ✅ ทุก finding | `causation[]` พร้อม `file:line:col` ตั้งแต่ producer ถึงจุดพัง |
| Repro witness | ✅ ข้อความ + syntax check | ไม่ execute; deadlock/recursion demo ติดธง `safe_to_execute: false` |
| Suggested patch | ✅ ผ่าน re-parse + re-scan | patch ที่ทำให้ finding ใหม่โผล่ถูกปฏิเสธ |
| Cache | ✅ sqlite3 WAL | key = path + size + mtime_ns + digest + ruleset bytecode + schema |
| นับ finding ที่ถูก suppress | ✅ | บรรทัดสรุปแสดง `N suppressed` |
| รู้ type ข้ามไฟล์ | ⚠️ จำกัด | รู้เฉพาะ annotation, literal, producer ในไฟล์เดียวกัน และ API ที่ hard-code |
| พิสูจน์ว่าโปรแกรมถูก | ❌ ไม่ใช่เป้าหมาย | เป็น static analysis 13 กฎ ไม่ใช่ formal verification |

</details>

---

<a id="metrics" name="metrics"></a>
## 📈 ผลวัดจริงบนเครื่องนี้

วัดบน Windows 11 (AMD64) และ WSL2 Ubuntu รวมสาม interpreter

| สถานการณ์ | 3.12.10 (Windows) | 3.14.4 (WSL2 Ubuntu) | 3.15.0rc2 (Windows) |
|---|---|---|---|
| สแกน `src/` 23 ไฟล์ (cold, `--no-cache`) | **642 ms** | **725 ms** | **510 ms** |
| สแกนซ้ำ (warm cache) | **12.3 ms** | **270 ms**\* | **9.9 ms** |
| Test suite (`unittest`, 268 tests) | **6.9 s OK** | **17.3 s OK** | **6.8 s OK** |
| DEFECT / WARNING / ADVICE บน `src/` | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |

\* WSL อ่าน repo ผ่าน `/mnt/l` (drvfs) ทำให้ต่อ sqlite ช้ากว่าปกติ ไม่ใช่ช้าที่ engine

ขนาด engine (`src/ait`, 23 ไฟล์) **7,080 บรรทัด** · Runtime dependencies **0**

ตัวเลขจะต่างตามเครื่องและขนาดไฟล์ สัดส่วน cold/warm บอกได้มากกว่าค่าสัมบูรณ์

---

## 🧠 หลักการทำงาน

<details>
<summary><b>🏗️ คลิกเพื่อขยาย: pipeline, cache, regression diff และ witness</b></summary>

```text
discover ──► parse ──► scope tree ──► call graph ──► data flow
                 │                                        │
                 └────────────► 13 rules ◄────────────────┘
                                    │
                     findings ──────┼──────► causation trace
                                    │──────► repro witness
                                    └──────► suggested patch (re-parsed)
```

- **Cache** เป็น sqlite3 (WAL) เก็บตาม `path + size + mtime_ns + BLAKE2b digest + ruleset fingerprint + schema`
  digest ถูกเทียบตอน lookup จริง ดังนั้น rewrite ที่คืน size/mtime เดิม (`git checkout`, `touch -r`, restore backup)
  จะไม่หยิบผลของเนื้อหาเก่ากลับมาใช้
  ruleset fingerprint แฮช bytecode ของทุก rule ไม่ใช่แค่ชื่อ ถ้าแก้ตัว detector ผลเก่าจะถูก invalidate
  ถ้าไฟล์ DB เสียหาย (magic header ไม่ตรง) จะทิ้งแล้วสร้างใหม่หนึ่งครั้ง ถ้ายังไม่หายจะปิด cache ไปเลยแทนที่จะพังหรือวนซ้ำ
- **Regression diff** เทียบด้วยจำนวน finding ต่อ `(rule, symbol)` ไม่ใช่ line number
  บรรทัดเลื่อนจึงไม่ทำให้เกิด finding ผี
- **Witness** ถูกสร้างเป็นข้อความและ parse ตรวจ syntax เท่านั้น ไม่มีการ execute
  (deadlock/recursion demo จะติดธง `safe_to_execute: false`)

</details>

---

## 🧱 โครงสร้าง

<details>
<summary><b>🗂️ คลิกเพื่อขยาย: ผังไฟล์ทั้งโปรเจกต์</b></summary>

```text
AiT-Test/
├── cli.py                 # entrypoint แบบ pytest-style
├── mcp.py                 # entrypoint JSON-RPC over stdio
├── pyproject.toml         # packaging (license = MIT)
├── LICENSE                # MIT license text
├── .github/workflows/
│   └── ci.yml             # 4 jobs: test matrix (12) + package + lint + security
├── .github/dependabot.yml # SHA-pinned actions, updated weekly
├── src/ait/
│   ├── engine.py          # discover / scan / compare_findings / verify_fix
│   ├── models.py          # Diagnostic, Patch, Witness, ScanResult
│   ├── context.py         # guard detection, suppression, patch builders
│   ├── cache.py           # sqlite3 cache + corruption recovery
│   ├── reporter.py        # terminal renderer
│   ├── slicer.py          # ait_slice_context
│   ├── flow/              # scope tree, call graph, data flow, witness
│   └── rules/             # 13 rules
├── tests/                 # unittest + fixtures
└── docs/                  # demo.gif, demo.txt, assets/
```

| ชั้น | ไฟล์ | หน้าที่ |
| :--- | :--- | :--- |
| Frontend | `cli.py` | parse flags, render, `--fix` session, exit code |
| Frontend | `mcp.py` | JSON-RPC framing, tool schema/validation, stdio loop |
| Core | `engine.py` | discover → parse → analyze → cache → aggregate |
| Core | `context.py` | guard/dominance lookup, suppression, evidence/patch builders |
| Flow | `flow/scope_tree.py` | symbol table + lexical scopes + binding resolution |
| Flow | `flow/call_graph.py` | call sites, lock regions, spawn sites |
| Flow | `flow/data_flow.py` | backward tracing of optional/None sources |
| Flow | `flow/witness.py` | minimal repro text + syntax validation |
| Rules | `rules/*.py` | 13 detectors (Z/R/A/M/N/H) |
| Infra | `cache.py` | sqlite3 WAL, digest/ruleset keyed, prune, `--lf` index |
| Infra | `models.py` | immutable Diagnostic/Patch/Witness, exit codes, SCHEMA_VERSION |
| CI | `.github/workflows/ci.yml` | test matrix, self-scan, stdlib-only run, integration tests |
| CI | `tests/test_cli_integration.py` | ขับ `cli.py` / `mcp.py` จริงผ่าน subprocess แล้ว assert exit code / JSON |

</details>

---

<a id="tests" name="tests"></a>
## 🧪 การทดสอบ

ไม่ต้องใช้ pytest:

```bash
python -m unittest discover -s tests -v
```

fixtures มีทั้ง `bad_*.py` (ต้องเจอ) และ `clean_*.py` (ต้องเงียบ)
ตัว suite ยังตรวจด้วยว่า engine import แค่ standard library (`python -S` ก็ยังรันได้)

```text
Ran 268 tests in ~7s
OK
```

<details>
<summary><b>🖥️ คลิกเพื่อขยาย: ผลบน 8 interpreter + ตารางแวดล้อม</b></summary>

ยืนยันบน 8 interpreter (7 บน Windows 11 + 1 บน WSL2 Ubuntu): ทุกตัวได้ 268 tests OK, `src/` สะอาด, exit 0:

| Interpreter | ที่มา |
|---|---|
| CPython 3.10.21 | uv-managed (Windows 11) |
| CPython 3.11.16 | uv-managed (Windows 11) |
| CPython 3.12.10 | Windows 11 (system) |
| CPython 3.12.14 | `L:\.Python312` |
| CPython 3.13.15 | `L:\.Python313` |
| CPython 3.14.7 | `L:\.Python314` |
| CPython 3.15.0rc2 | `L:\.Python315` |
| CPython 3.14.4 | WSL2 Ubuntu |

```text
3.10.21  → Ran 268 tests in 7.152s OK
3.11.16  → Ran 268 tests in 6.990s OK
3.12.10  → Ran 268 tests in 6.928s OK
3.12.14  → Ran 268 tests in 6.698s OK
3.13.15  → Ran 268 tests in 6.724s OK
3.14.4 (WSL2 Ubuntu) → Ran 268 tests in 17.332s OK
3.14.7   → Ran 268 tests in 7.319s OK
3.15.0rc2 → Ran 268 tests in 6.769s OK
```

ทั้ง 8 ตัวสแกน `src/` ได้ 23/23 ไฟล์ ไม่พบ finding และ exit 0
ส่วน `tests/fixtures` ให้ exit 1 ตามที่ควรเป็น (DEFECT 11 · WARNING 4 · ADVICE 1)

บน 3.15 รันซ้ำด้วย `-W error::DeprecationWarning` แล้วยังผ่านทั้ง 268 tests
แปลว่า engine ไม่แตะ API ที่กำลังจะถูกถอดในอนาคต และ `python -S` (ไม่มี site)
ก็สแกนได้ปกติ

</details>

---

<a id="ci" name="ci"></a>
## 🔄 CI (GitHub Actions)

CI ใช้ GitHub Actions โดย pin ทุก action ด้วย **commit SHA** (ไม่ใช่ tag ลอย) และมี Dependabot อัปเดตให้ทุกสัปดาห์

<details>
<summary><b>🧪 คลิกเพื่อขยาย: job <code>test</code> บน 12 environment</b></summary>

| Step / test | ตรวจอะไร | เกณฑ์ผ่าน |
|---|---|---|
| `python -m unittest discover -s tests` | 268 tests บน interpreter นั้น | ต้อง `OK` |
| `-W error::DeprecationWarning` (เฉพาะ 3.15) | ใช้ API ที่กำลังจะถูกถอดหรือไม่ | ต้อง `OK` |
| `python cli.py src --no-cache` | self-scan ของ engine เอง | exit 0 · DEFECT/WARNING/ADVICE = 0 |
| `python -S cli.py src --no-cache` | ไม่มี site-packages เลย | exit 0 → ยืนยันว่าไม่พึ่ง third-party |
| `tests/test_cli_integration.py` (สแกน `tests/fixtures` ผ่าน `cli.py`) | corpus ที่ตั้งใจให้พัง | exit **1** + มี `DEFECT` ในผล |
| `tests/test_mcp_protocol.py` (`mcp.py` ผ่าน stdio) | JSON-RPC จริง (`initialize` → `tools/call` → `shutdown`) | `isError:false` · 0 findings · `shutdown` ตอบกลับ |

`tests/test_cli_integration.py` และ `tests/test_mcp_protocol.py` เรียก `cli.py` / `mcp.py` ผ่าน `subprocess` เหมือนผู้ใช้จริง ไม่ได้ import เข้ามาเรียกฟังก์ชันภายใน
จึงจับ regression ที่อยู่ระดับ entrypoint ได้ (เช่น exit code เพี้ยน, stdout ปน log, protocol frame แตก)

</details>

<details>
<summary><b>📦 คลิกเพื่อขยาย: job <code>package</code> / <code>lint</code> / <code>security</code></b></summary>

| Job | เครื่องมือ | ตรวจอะไร | เกณฑ์ผ่าน |
|---|---|---|---|
| `package` | `pip wheel` + venv สะอาด | สร้าง wheel, ติดตั้งด้วย `--no-index --no-deps`, รัน `ait --version`, `ait sample.py`, `ait-mcp` นอก source tree | exit 0 ทุกคำสั่ง |
| `package` | `importlib.metadata` | metadata ต้องไม่มี `Requires-Dist` และ import ได้ครบ 5 module | `requires == None` |
| `lint` | **Ruff 0.17.2** | `E4/E7/E9/F` (undefined name, unused import, syntax) | 0 findings |
| `lint` | **Bandit 1.9.4** | ช่องโหว่ระดับ medium ขึ้นไปใน `src`, `cli.py`, `mcp.py` | 0 findings |
| `lint` | `compileall` | ทุก module compile ผ่าน | exit 0 |
| `lint` | **actionlint 2.2.0** | syntax + expression ของ workflow เอง | exit 0 |
| `security` | **OSV-Scanner 2.7.0** | dependency surface ทั้ง repo เทียบฐาน OSV | `No issues found` |
| `security` | shell guard | ถ้ามี `requirements*.txt` / `uv.lock` / `poetry.lock` โผล่มา ต้องหยุดและไปสแกนจริง | ไม่พบ lockfile |

Ruff จับอะไรที่ engine เองจับไม่ได้ รอบนี้มันเจอ `F821 Undefined name 'List'` ใน `rules/registry.py`
(ซ่อนอยู่ใต้ `from __future__ import annotations` จึงไม่พังตอนรัน แต่พังทันทีถ้ามีคนเรียก `get_type_hints`) และแก้แล้ว

Bandit รายงาน `B101 assert` อยู่ 2 จุด (`cache.py`, `cli.py`) ซึ่งเป็น **low severity**:
`assert` ทั้งสองใช้เป็น internal invariant หลัง type narrowing
จึงตั้งเกณฑ์ไว้ที่ `-ll` (medium ขึ้นไป) และปล่อย low ไว้โดยเจตนา

</details>

<details>
<summary><b>🔐 คลิกเพื่อขยาย: ทำไมยังไม่เปิด CodeQL / dependency-review</b></summary>

ทั้งสองตัวต้องใช้ **GitHub Advanced Security** ถ้า repo เป็น private (CodeQL ใช้ Code Scanning, dependency-review ใช้ dependency graph API)

ถ้า repo เป็น public และต้องการเพิ่ม ใส่ไฟล์ `.github/workflows/codeql.yml` แยกได้เลย:

```yaml
name: CodeQL
on:
  push:
    branches: [main]
  schedule:
    - cron: "0 3 * * 1"
permissions:
  contents: read
  security-events: write
jobs:
  analyze:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
      - uses: github/codeql-action/init@793b778da9c9275a0f276522b27cb5fc14bba279 # v4
        with:
          languages: python
          queries: security-and-quality
      - uses: github/codeql-action/analyze@793b778da9c9275a0f276522b27cb5fc14bba279 # v4
```

</details>

---

## ⚠️ ข้อจำกัด

> [!WARNING]
> อ่านส่วนนี้ก่อนใช้จริง เครื่องมือนี้ตอบคำถามแคบ ๆ ได้ดี แต่ไม่ได้แทนที่ type checker, test suite หรือ code review

- เป็น **static analysis** ไม่ได้รันโค้ด จึงไม่มีทางรู้ค่า runtime จริง
- ไม่มี type inference เต็มรูปแบบ จึงรู้เฉพาะ annotation, literal, producer ในไฟล์เดียวกัน และ API ที่ hard-code ไว้
- ข้าม module: ถ้า optional มาจากอีกไฟล์ที่ไม่ได้สแกนด้วย จะไม่เห็น
- `AIT-R01` รายงานเฉพาะเมื่อมีหลักฐาน concurrency ถ้าโปรเจกต์ spawn thread ผ่าน abstraction แปลก ๆ อาจพลาด
- witness เป็น "ตัวอย่างที่ทำให้เกิดอาการ" ไม่ใช่ test case ที่พร้อมใส่ CI
- patch เป็นการแนะนำแบบ syntactic บางครั้งอาจไม่ตรงกับสถาปัตยกรรมที่ต้องการ (เช่น ควรใช้ `raise` หรือ `return` ก็แล้วแต่ context)

ถ้ากังวลเรื่อง false positive มากกว่า false negative ให้เริ่มจาก `--maxfail 5` แล้วดูทีละอัน

---

<a id="credits" name="credits"></a>
## 🏅 เครดิตและยศ

<p align="center"><b>xTanTHaix</b><br><i>Project Architect · Maintainer · ผู้ถือลิขสิทธิ์ AiT-test</i></p>

<details>
<summary><b>🎖️ คลิกเพื่อขยาย: ยศและเครื่องหมายที่ผูกกับผลงานจริงในโปรเจกต์นี้</b></summary>

| ยศ | ที่มา |
| :--- | :--- |
| 🏛️ Architect of the 13 Rules | วางชุดกฎ 13 ข้อ (Z/R/A/M/N/H) และนโยบาย "DEFECT ต้องมีหลักฐาน ไม่งั้นลดเป็น WARNING" |
| 🧪 Eight-Interpreter Verifier | ยืนยัน 268 tests บน CPython 3.10 → 3.15 ทั้ง Windows 11 และ WSL2 Ubuntu |
| 🛡️ Zero-Dependency Keeper | engine 23 ไฟล์ 7,080 บรรทัด ที่ไม่มี dependency ตอนรันและทำงาน offline ได้ |
| 🤖 MCP Toolsmith | สัญญา JSON-RPC 2.0 สาม tools พร้อม validation, `structuredContent` และ lifecycle ที่ปิดได้จริง |
| 🔁 CI Gatekeeper | 4 jobs / 15 runs: test matrix 12 environment + package + lint (ruff, bandit, actionlint) + OSV |

</details>

---

<p align="center"><a href="https://ko-fi.com/xtanthaix"><img alt="Ko-fi: buy me a coffee" width="46%" src="https://img.shields.io/badge/Ko--fi-buy%20me%20a%20coffee-FF5E5B?style=flat-square&logo=kofi&logoColor=white"></a></p>

<p align="center"><i>เขียนและดูแลโดย <a href="https://ko-fi.com/xtanthaix">xTanTHaix</a> · ถ้า AiT-test ช่วยประหยัดเวลาคุณได้ เลี้ยงกาแฟผู้พัฒนาสักแก้วได้ที่ <a href="https://ko-fi.com/xtanthaix">ko-fi.com/xtanthaix</a> ☕</i></p>

---

## 📜 License

สัญญาอนุญาต MIT ประกาศไว้ใน `pyproject.toml` (`license = "MIT"`) และข้อความเต็มอยู่ที่ [`LICENSE`](LICENSE)

---

<p align="center"><i>Static invariants, causation traces and verified patches, with zero runtime dependencies.</i></p>
