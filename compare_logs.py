"""Fetch installed modules from runbot log"""

from collections import defaultdict
import argparse
import re

# call the script like this:
# python compare_logs.py ___AFTER___ ~/before.log ~/after.log
parser = argparse.ArgumentParser()
parser.add_argument(
    "after",
    help="keyword to place in log after which to analyze",
    type=str,
)
parser.add_argument("file1", help="path of file 1", type=str)
parser.add_argument("file2", help="path of file 2", type=str)
args = parser.parse_args()

ids_1_map = defaultdict(dict)
ids_2_map = defaultdict(dict)


def clean_log(content, ids_map):
    """Takes the given Odoo log and remove noise to make it diffable (dates, ...)"""
    lines = content.splitlines()
    final_lines = []
    for cur_line in lines:
        final_lines.append(cur_line)
        if args.after in cur_line:
            final_lines = []
    content = "\n".join(final_lines)

    def replace_in_ids(match):
        field_name = match.group(1).replace('"', "")
        ids = ids_map[field_name]
        found_ids = sorted([int(i) for i in match.group(2).split(", ")])
        for found_id in found_ids:
            if found_id not in ids:
                ids[found_id] = max(ids.values()) + 1 if ids else 1
        return f"{field_name} IN ({', '.join('fake_' + str(ids[found_id]) for found_id in found_ids)})"

    def replace_id_eq_any(match):
        field_name = f"{match.group(1)}.{match.group(2)}".replace('"', "")
        ids = ids_map[field_name]
        found_ids = sorted([int(i) for i in match.group(3).split(", ")])
        for found_id in found_ids:
            if found_id not in ids:
                ids[found_id] = max(ids.values()) + 1 if ids else 1
        return f"{field_name} IN ({', '.join('fake_' + str(ids[found_id]) for found_id in found_ids)})"

    def replace_equal_ids(match):
        field_name = match.group(1).replace('"', "")
        ids = ids_map[field_name]
        found_id = int(match.group(2))
        if found_id not in ids:
            ids[found_id] = max(ids.values()) + 1 if ids else 1
        return f"{field_name} = fake_{str(ids[found_id])}"

    def replace_select(match):
        parts = match.group(1).split(", ")
        parts.sort()
        return f"SELECT {', '.join(parts)} FROM"

    # remove header "2024-07-03 13:59:22,488 1499449 INFO master-init-data-5--seb-e-t "
    content = re.sub(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} \d+ \w+ [\w?.-]+ ", "", content)
    # remove query timing: "odoo.sql_db: [0.204 ms] query:"
    content = re.sub(r"odoo.sql_db: \[\d+\.\d+ ms\] query:", "odoo.sql_db: [x ms] query:", content)
    # remove modules loaded (timings)
    content = re.sub(r".*odoo\.modules\.loading:.*", "", content)
    # remove registry loaded (timings)
    content = re.sub(r".*Registry loaded in.*", "", content)
    # remove odoo.service.server (timings, counter)
    content = re.sub(r".*odoo\.service\.server.*", "", content)
    # remove odoo.addons.base.models.ir_actions_report
    content = re.sub(r".*odoo\.addons\.base\.models\.ir_actions_report*", "", content)
    # remove odoo.addons.base.models.ir_qweb
    content = re.sub(r".*odoo\.addons\.base\.models\.ir_qweb.*", "", content)
    # remove odoo.addons.base.models.res_device:
    content = re.sub(r".*odoo\.addons\.base\.models\.res_device.*", "", content)
    # remove odoo.models.unlink
    content = re.sub(r".*odoo\.models\.unlink.*", "", content)
    # remove Simulating signal changes during tests
    content = re.sub(r".*Simulating signal changes during tests.*", "", content)
    # remove Generating routing map
    content = re.sub(r".*Generating routing map.*", "", content)
    # remove Login successful for
    content = re.sub(r".*Login successful for.*", "", content)
    # remove Signup email sent for user
    content = re.sub(r".*Signup email sent for user.*", "", content)
    # remove werkzeug:
    content = re.sub(r".*werkzeug: .*", "", content)
    # remove odoo.tests.stats
    content = re.sub(r".*odoo\.tests\.stats.*", "", content)
    # remove odoo.tests.result
    content = re.sub(r".*odoo\.tests\.result:.*", "", content)
    # remove "odoo: " lines
    content = re.sub(r"^odoo: .*", "", content)
    # remove test line with "======================================================================"
    content = re.sub(r".*={70}.*", "", content)
    # kill new lines and extra spaces (in queries)
    content = re.sub(r"\s+", " ", content)
    # remake new lines at the proper places
    content = re.sub(r" (odoo(\.\w+)*:)", r"\n\1", content)
    # remove Query count less than expected
    content = re.sub(r".*Query count less than expected.*", "", content)
    # remove Query count more than expected
    content = re.sub(r".*Query count more than expected.*", "", content)
    # remove unlink ids
    content = re.sub(
        r"User #\d+ deleted ([\w\.]+) records with IDs: \[\d+\]",
        r"User #x deleted \1 records with IDs: [x]",
        content,
    )
    # remove "INSERT INTO "bus_bus"" lines (should be improved to remove dynamic parts instead, can be commented if necessary)
    content = re.sub(r'.*INSERT INTO "bus_bus".*', "", content)
    # remove "INSERT INTO" lines (should be improved to remove dynamic parts instead, can be commented if necessary)
    content = re.sub(r".*INSERT INTO.*", "", content)
    # discard irrelevant ids in "IN" queries
    content = re.sub(r"(\"?\w+\"?.\"?\w+\"?) IN \((\d+(, \d+)*)\)", replace_in_ids, content)
    # discard irrelevant ids in "=" queries
    content = re.sub(r"(\"?\w+\"?.\"?\w+\"?) ?= ?(\d+)", replace_equal_ids, content)
    # discard irrelevant ids in "FROM ... WHERE ... = ANY (ARRAY[...])" queries
    content = re.sub(r"FROM (\"?\w+\"?) WHERE (\"?\w+\"?) = ANY \(ARRAY\[(\d+(, \d+)*)\]\)", replace_id_eq_any, content)
    # kill what looks like a datetime in format 2024-07-17 13:32:46.462594
    content = re.sub(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(\.\d{6})?", "fake_date", content)
    # kill what looks like a datetime in format 2024-08-16T16:54:12
    content = re.sub(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", "fake_date", content)
    # kill what looks like email stuff <351129566921887.1721223167.391957044601440-openerp-message-notify@seb-laptop>
    content = re.sub(r"<\d+\.\d+.\d+-openerp-message-notify@.+>", "fake@email", content)
    # kill id following fake email: 'fake@email', 42,
    content = re.sub(r"'fake@email', \d+,", "fake_id", content)
    # sort field names in SELECT
    content = re.sub(
        r"SELECT (\"\w+\"\.\"\w+\"(, \"\w+\"\.\"\w+\")*) FROM", replace_select, content
    )
    # remove query header
    content = re.sub(r"odoo\.sql_db: \[x ms\] query: (.*)", r"\1;", content)
    return content


with open(args.file1, "rt", encoding="utf-8") as f1:
    clean1 = clean_log(f1.read(), ids_1_map)
with open(args.file2, "rt", encoding="utf-8") as f2:
    clean2 = clean_log(f2.read(), ids_2_map)

# print(clean2)

lines1 = clean1.splitlines()
lines2 = clean2.splitlines()

in1notin2 = []
copy_lines2 = lines2.copy()
for line in lines1:
    try:
        copy_lines2.remove(line)
    except ValueError:
        in1notin2.append(line)

in2notin1 = []
copy_lines1 = lines1.copy()
for line in lines2:
    try:
        copy_lines1.remove(line)
    except ValueError:
        in2notin1.append(line)

print(f"\n\n-- Extra queries in: {args.file1}")
for line in sorted(in1notin2):
    print(f"\n{line}")

print(f"\n\n-- Extra queries in: {args.file2}")
for line in sorted(in2notin1):
    print(f"\n{line}")
