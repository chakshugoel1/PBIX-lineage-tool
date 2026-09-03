import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pbixray import PBIXRay
from core import lineage_lib as ll
import config

pbix_path = config.PBIX_PATH
dataflow_folder = config.DATAFLOW_FOLDER

model = PBIXRay(pbix_path)
entries = {}
for _, row in model.power_query.iterrows():
    entries[row["TableName"]] = str(row["Expression"])
for _, row in model.m_parameters.iterrows():
    entries[row["ParameterName"]] = str(row["Expression"])

pbix_universe = ll.Universe(entries)
global_params = pbix_universe.build_global_param_values()
direct, enumerators, unrecognized = ll.analyze_direct_dataflow_bindings(pbix_universe, global_params)

print("Direct dataflow bindings found in PBIX:", len(direct))
print("Enumerators found in PBIX:", len(enumerators), list(enumerators.keys()))

dataflows = ll.load_dataflows(dataflow_folder)
entity_index = ll.build_entity_index(dataflows)
name_index = ll.build_name_index(dataflows)
print("\nLoaded dataflows:", len(dataflows))
print("Entity index size (distinct entity names):", len(entity_index))

test_tables = ["301_FACT_OUTPROD", "30210_FACT_INVOICE_DOCUMENT", "101_DIRECTION",
               "GDD_APPLICATION", "30x_Dim_Factures", "000_CALENDAR",
               "511_CONTINENT_TRANS" if "511_CONTINENT_TRANS" in entries else "100_ORGANISATION"]

print("\nSample of table names containing 'INVOICE_DOCUMENT_LINE':")
for n in entries:
    if "INVOICE_DOCUMENT_LINE" in n:
        print(" -", n)

entity_of = ll.build_entity_of(pbix_universe)
cache = {}
for t in test_tables:
    if t not in entries:
        print(f"\n=== {t}: NOT FOUND in PBIX entries ===")
        continue
    lvl1 = ll.resolve_pbix_lineage(t, pbix_universe, direct, entity_of, cache, set())
    print(f"\n=== {t} ===")
    print("Level1 result:", lvl1)
    if lvl1:
        entity = lvl1.get("entity") or t
        l1_stem = lvl1["dataflow"]
        phys = ll.resolve_physical_source(l1_stem, entity, dataflows, entity_index, name_index=name_index)
        print("Physical resolution:", phys)
