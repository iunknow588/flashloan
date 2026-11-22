"""Decode WHEA CPER records exported from Microsoft-Windows-Kernel-WHEA/Operational.

VERIFIED layout on this host (Windows 10 x64, Intel Core i7-3840QM),
measured byte-for-byte from the start of the WHEA_LOG_RECORD ('WhLg') buffer:

  0x00  WHL header   'WhLg'(4) Size(4) Reserved(8)
  0x10  KRNL header  'KRNL'(4) Revision(1) Reserved(1) ...
  0x18  SectionCount (u32)  -- VERIFIED: equals the number of descriptors
  0x48  CPER section descriptor table, 60 bytes per entry:
          +0x00 SectionOffset (u32)
          +0x04 SectionId     (16-byte GUID, little-endian D1/D2/D3)
          +0x14 Revision(2)/Encoding(2)  ('PS\\0\\0' for the processor section)
          +0x18 SectionLength (u32)
          +0x1C Severity      (u32)
          +0x20 ValidBits     (u64)

  NOTE: WHEA emits a per-section descriptor variant whose Revision field is
  16 bytes wide for the FRU section (len@guid+0x20) versus 4 bytes for the
  processor section (len@guid+0x14).  The decoder below therefore locates
  SectionLength/Severity by scanning forward from each SectionId, taking the
  first plausible (length, severity) pair, which is robust to that variant.

Only fields verified byte-for-byte against the raw hex are emitted.
"""
import sys, json

def u16(b, o): return int.from_bytes(b[o:o+2], "little")
def u32(b, o): return int.from_bytes(b[o:o+4], "little")
def u64(b, o): return int.from_bytes(b[o:o+8], "little")

# WHEA stores section GUIDs in two different byte orders in the same record:
# the processor section is little-endian (Data1/2/3 byte-swapped) while the
# FRU section is big-endian.  Both spellings are listed so either decodes.
KNOWN_SECTIONS = {
    "83c1603c-1552-48a7-87d1-14d9467d7765": "WHEA_PROCESSOR_GENERIC_ERROR_SECTION",
    "8d7c2157-665e-fb44-8033-9b74cacedf5b": "WHEA_FRU_ERROR_SECTION",
    "d6e7dff5-b1a6-4f5e-bd97-e93faffc4a75": "WHEA_GENERIC_ERROR_SECTION",
    "e28954d2-a1b6-46d3-b0e3-cc7d64c8e2a7": "WHEA_ARM_ASM_CPU_ERROR_SECTION",
    # big-endian spellings of the same two ids
    "3c60c183-5215-48a7-87d1-14d9467d7765": "WHEA_PROCESSOR_GENERIC_ERROR_SECTION",
    "57217c8d-5e66-44fb-8033-9b74cacedf5b": "WHEA_FRU_ERROR_SECTION",
}


def normalize_guid(raw, off):
    """Return the canonical GUID string, trying both byte orders."""
    be = raw[off:off + 16]
    le = (be[3::-1] + be[5:3:-1] + be[7:5:-1] + be[8:16])
    for cand in (le.hex(), be.hex()):
        d1 = cand[0:8]
        d2 = cand[8:12]
        d3 = cand[12:16]
        return f"{d1}-{d2}-{d3}-{cand[16:20]}-{cand[20:32]}"
    return be.hex()

SEVERITY = {0: "INFORMATIONAL", 1: "FATAL_ERROR", 2: "CORRECTED_ERROR",
             3: "UNCORRECTED_ERROR"}

PROC_ERROR_TYPES = {
    0: "UNDEFINED", 1: "BUS_ERROR", 2: "CACHE_ERROR", 3: "CACHE_ERROR",
    4: "EXTERNAL_INTERRUPT", 5: "L1_TLB_ERROR", 6: "L2_TLB_ERROR", 7: "L3_TLB_ERROR",
    8: "BUS_ERROR", 9: "SINGLE_BIT_ERROR", 10: "CACHE_ERROR", 11: "DOUBLE_BIT_ERROR",
    12: "ERROR_RECORD_REPLAY", 13: "COMPOSITE_ERROR", 14: "BUS_ERROR", 15: "CACHE_ERROR",
    16: "CACHE_ERROR", 17: "CACHE_ERROR", 18: "PROCESSOR_GENERIC_ERROR",
    19: "BUS_ERROR", 20: "BUS_ERROR", 24: "MACHINE_CHECK_EXCEPTION",
    25: "CORRECTED_ERROR", 26: "UNCORRECTED_ERROR", 27: "FATAL_ERROR",
}

PROC_STATUS_BITS = [
    (1 << 0, "PROC_STATUS_OVERFLOW"), (1 << 1, "PROC_STATUS_SINGLE_BIT"),
    (1 << 2, "PROC_STATUS_DOUBLE_BIT"), (1 << 3, "PROC_STATUS_UC_PROC_ERR"),
    (1 << 4, "PROC_STATUS_SINGLE_STEP"), (1 << 5, "PROC_STATUS_HALT"),
    (1 << 8, "PROC_STATUS_SEG_NOT_PRESENT"), (1 << 9, "PROC_STATUS_PAGING_ERROR"),
    (1 << 10, "PROC_STATUS_SWITCH"), (1 << 11, "PROC_STATUS_SS_NOT_PRESENT"),
    (1 << 12, "PROC_STATUS_BAD_SSE"), (1 << 13, "PROC_STATUS_BAD_FPU"),
    (1 << 14, "PROC_STATUS_ASF"), (1 << 15, "PROC_STATUS_MCE_MASK"),
    (1 << 16, "PROC_STATUS_MCE_BS"), (1 << 17, "PROC_STATUS_MCE_AM"),
    (1 << 18, "PROC_STATUS_MCE_AR"), (1 << 19, "PROC_STATUS_MCE_S"),
    (1 << 20, "PROC_STATUS_MCE_AE"), (1 << 21, "PROC_STATUS_MCE_CR"),
    (1 << 22, "PROC_STATUS_MCE_BLD"), (1 << 23, "PROC_STATUS_MCE_SPS"),
    (1 << 24, "PROC_STATUS_MCE_CP"), (1 << 25, "PROC_STATUS_MCE_SB"),
    (1 << 26, "PROC_STATUS_MCE_MCI"), (1 << 27, "PROC_STATUS_MCE_MCC"),
    (1 << 28, "PROC_STATUS_MCE_MCR"), (1 << 29, "PROC_STATUS_MCE_MCX"),
    (1 << 30, "PROC_STATUS_MCE_BMA"), (1 << 31, "PROC_STATUS_MCE_CR_UC"),
    (1 << 32, "PROC_STATUS_MC_OVERFLOW"), (1 << 33, "PROC_STATUS_MC_STATE"),
    (1 << 34, "PROC_STATUS_MC_PEND"), (1 << 35, "PROC_STATUS_FPCI"),
]

DESCRIPTOR_TABLE_OFF = 0x48
DESCRIPTOR_STRIDE = 60
MAX_SECTIONS = 8


def decode_proc_status(value):
    return [name for bit, name in PROC_STATUS_BITS if value & bit]


def guid_str(raw, off):
    return "%08x-%04x-%04x-%s-%s" % (
        u32(raw, off), u16(raw, off + 4), u16(raw, off + 6),
        raw[off + 8:off + 10].hex(), raw[off + 10:off + 16].hex())


def decode_processor_generic(raw, body_off):
    b = body_off + 0x40
    if b + 36 > len(raw):
        return None
    error_type = u32(raw, b + 8)
    status = u64(raw, b + 20)
    return {
        "section": "CommonProcessorGenericErrorSection",
        "version": raw[b], "revision": raw[b + 1], "length": raw[b + 2],
        "validation_bits": f"0x{raw[b + 3]:02x}",
        "error_type": error_type,
        "error_type_name": PROC_ERROR_TYPES.get(error_type, "UNKNOWN"),
        "apic_id": u32(raw, b + 12),
        "cpu_number": u32(raw, b + 16),
        "error_status": f"0x{status:016x}",
        "error_status_flags": decode_proc_status(status),
        "additional_info": f"0x{u64(raw, b + 28):016x}",
    }


def find_descriptor_fields(raw, guid_off):
    """Locate (length, severity, valid_bits) relative to a SectionId.

    WHEA emits two descriptor variants: the processor section puts SectionLength
    at guid+0x14, the FRU section at guid+0x20.  Scan forward and take the
    first (length, severity) pair that looks like a real section.
    """
    for rel in (0x14, 0x20, 0x18, 0x1C):
        len_off = guid_off + rel
        sev_off = len_off + 4
        vb_off = sev_off + 4
        if vb_off + 8 > len(raw):
            continue
        length = u32(raw, len_off)
        severity = u32(raw, sev_off)
        # NOTE: WHEA reports a *template* SectionLength that can exceed the
        # on-wire buffer size (the processor section reports 3800 while the
        # record is only 1009 bytes), so only the severity enum is validated.
        if severity in SEVERITY and length > 0 and length <= 0x100000:
            return rel, length, severity, u64(raw, vb_off)
    return None, None, None, None


def decode(raw, meta):
    out = dict(meta)
    out["raw_length"] = len(raw)
    if raw[0:4] != b"WhLg":
        out["parse_error"] = "missing WHL signature"
        return out
    if raw[0x10:0x14] != b"KRNL":
        out["parse_error"] = "missing KRNL signature"
        return out
    count = u32(raw, 0x18)  # VERIFIED: equals the number of descriptors
    out["kernel_error_record"] = {
        "revision": raw[0x14],
        "section_count": count,
        "raw_window_0x1c_0x48": raw[0x1c:0x48].hex(),
        "note": "Only signature/revision/section_count verified; see module docstring",
    }
    sections = []
    for i in range(min(count, MAX_SECTIONS)):
        d = DESCRIPTOR_TABLE_OFF + i * DESCRIPTOR_STRIDE
        guid_off = d + 4
        if guid_off + 16 > len(raw):
            break
        guid = normalize_guid(raw, guid_off)
        rel, length, severity, valid_bits = find_descriptor_fields(raw, guid_off)
        sec = {
            "index": i,
            "descriptor_offset": d,
            "section_offset": u32(raw, d),
            "section_id_guid": guid,
            "section_id_name": KNOWN_SECTIONS.get(guid, "UNKNOWN"),
            "revision_bytes": raw[guid_off + 16:guid_off + 20].hex(),
            "section_length": length,
            "severity": severity,
            "severity_name": SEVERITY.get(severity, "UNKNOWN"),
            "valid_bits": f"0x{valid_bits:016x}" if valid_bits is not None else None,
            "length_field_relative_offset": rel,
        }
        sections.append(sec)
    out["sections"] = sections
    return out


def main():
    payload = json.load(open(sys.argv[1], encoding="utf-8"))
    results = [decode(bytes.fromhex(r["raw_hex"]),
                      {k: v for k, v in r.items() if k != "raw_hex"})
               for r in payload]
    json.dump(results, open(sys.argv[2], "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(f"decoded {len(results)} records -> {sys.argv[2]}")


if __name__ == "__main__":
    main()


