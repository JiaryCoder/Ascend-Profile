#!/usr/bin/env python3
"""
Fix Huawei Ascend trace_view.json for Perfetto compatibility (fast version).

Uses regex-based text replacement instead of full JSON parse for speed.

Problems fixed:
1. "ts": "xxx.yyy" (string) -> "ts": number (subtract baseline)
2. Flow "id" > 2^53 -> remap to small integers
"""

import re
import sys
import os
from decimal import Decimal

def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <input.json> [output.json]")
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else input_path.replace('.json', '_fixed.json')

    print(f"Input:  {input_path}")
    print(f"Output: {output_path}")
    fsize = os.path.getsize(input_path)
    print(f"File size: {fsize / 1e9:.2f} GB")
    print()

    # Pass 1: find min ts using regex (much faster than JSON parsing)
    print("Pass 1: Finding baseline timestamp...")
    ts_pattern = re.compile(rb'"ts":\s*"(\d+\.\d+)"')
    min_ts_decimal = None
    count = 0

    with open(input_path, 'rb') as f:
        while True:
            chunk = f.read(1 << 20)  # 1MB chunks
            if not chunk:
                break
            for m in ts_pattern.finditer(chunk):
                ts_str = m.group(1).decode()
                # Use integer arithmetic: "1778655613988086.250" -> split on dot
                parts = ts_str.split('.')
                ts_int = int(parts[0])  # microsecond part
                if min_ts_decimal is None or ts_int < min_ts_decimal:
                    min_ts_decimal = ts_int
                count += 1
            if count > 0 and count % 2000000 == 0:
                print(f"  Scanned {count} ts values...", flush=True)

    print(f"  Found {count} ts values")
    print(f"  Min ts (integer µs): {min_ts_decimal}")
    baseline = min_ts_decimal
    print(f"  Baseline: {baseline} µs")
    print()

    # Pass 2: regex-based replacement (streaming)
    print("Pass 2: Transforming...")

    # We need to handle:
    # 1. "ts": "1778655624745710.730" -> "ts": 10757624.730
    # 2. "id": 1778655624745710730 (number > 2^53) -> remap
    # 3. "id": "2940622373650432" (string id) -> keep as-is (already safe or remap)

    flow_id_map = {}
    next_flow_id = [1]

    def replace_ts(m):
        ts_str = m.group(1).decode()
        parts = ts_str.split('.')
        integer_part = int(parts[0]) - baseline
        frac_part = parts[1] if len(parts) > 1 else '0'
        return f'"ts":{integer_part}.{frac_part}'.encode()

    def replace_numeric_id(m):
        id_val = m.group(1).decode()
        id_int = int(id_val)
        if id_int > 9007199254740992:  # 2^53
            if id_val not in flow_id_map:
                flow_id_map[id_val] = next_flow_id[0]
                next_flow_id[0] += 1
            return f'"id":{flow_id_map[id_val]}'.encode()
        return m.group(0)

    def replace_string_id(m):
        id_val = m.group(1).decode()
        try:
            id_int = int(id_val)
            if id_int > 9007199254740992:
                if id_val not in flow_id_map:
                    flow_id_map[id_val] = next_flow_id[0]
                    next_flow_id[0] += 1
                return f'"id":{flow_id_map[id_val]}'.encode()
        except ValueError:
            pass
        return m.group(0)

    ts_re = re.compile(rb'"ts":\s*"(\d+\.\d+)"')
    numeric_id_re = re.compile(rb'"id":\s*(\d{16,})')
    string_id_re = re.compile(rb'"id":\s*"(\d+)"')

    bytes_written = 0
    with open(input_path, 'rb') as fin, open(output_path, 'wb') as fout:
        # Process in chunks, but be careful about chunk boundaries
        overlap = b''
        chunk_size = 4 << 20  # 4MB
        chunks_done = 0

        while True:
            chunk = fin.read(chunk_size)
            if not chunk:
                # Process remaining overlap
                if overlap:
                    data = overlap
                    data = ts_re.sub(replace_ts, data)
                    data = numeric_id_re.sub(replace_numeric_id, data)
                    data = string_id_re.sub(replace_string_id, data)
                    fout.write(data)
                    bytes_written += len(data)
                break

            data = overlap + chunk

            # Keep last 200 bytes as overlap to avoid splitting a JSON field
            safe_end = len(data) - 200
            if safe_end <= 0:
                overlap = data
                continue

            # Find last complete field boundary (comma or brace)
            while safe_end < len(data) and data[safe_end:safe_end+1] not in (b',', b'}', b']'):
                safe_end += 1
            if safe_end < len(data) and data[safe_end:safe_end+1] in (b',', b'}', b']'):
                safe_end += 1

            process = data[:safe_end]
            overlap = data[safe_end:]

            # Apply replacements
            process = ts_re.sub(replace_ts, process)
            process = numeric_id_re.sub(replace_numeric_id, process)
            process = string_id_re.sub(replace_string_id, process)

            fout.write(process)
            bytes_written += len(process)
            chunks_done += 1

            if chunks_done % 50 == 0:
                pct = (chunks_done * chunk_size) / fsize * 100
                print(f"  Progress: ~{pct:.0f}%...", flush=True)

    output_size = os.path.getsize(output_path)
    print(f"\nDone!")
    print(f"  Output size: {output_size / 1e9:.2f} GB")
    print(f"  Flow IDs remapped: {len(flow_id_map)}")
    print(f"  Baseline subtracted: {baseline} µs")
    print(f"  Max ts after fix: ~{(count and 'check in Perfetto' or 'N/A')}")
    print(f"\nOpen {output_path} in Perfetto to verify.")


if __name__ == '__main__':
    main()
