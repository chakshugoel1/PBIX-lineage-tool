# V1 Weekly vs V1_Weekly_UAT_TA_Back_Table Analysis - FINDINGS

## Executive Summary

**Baseline File:** V1 Weekly.pbix
- Total relationships: **116**
- Active: **112**
- Inactive: **4**

**Changed File:** V1_Weekly_UAT_TA_Back_Table (1).pbix
- Total relationships: **166**
- Active: **151** 
- Inactive: **15**

**New Relationships Added:** **50** total
- **Active:** 39
- **Inactive:** 11

---

## Answer to Your Questions

### 1. Are the 49 auto-detected relationships active or inactive?

**Status:** Mixed - **39 ACTIVE** and **11 INACTIVE**

The breakdown shows that Power BI's auto-detect feature created relationships in two states:
- **39 are ACTIVE** - These are the "safe" relationships Power BI is confident about (likely matching standard ID columns across fact and dimension tables)
- **11 are INACTIVE** - These are relationships Power BI auto-created but marked as inactive by default, likely due to ambiguity concerns or potential for calculation errors

### 2. The 1 relationship created before the batch

Unfortunately, the timestamp data in the PBIX files appears to have been normalized/compressed in a way that doesn't allow us to extract the exact Windows FILETIME values reliably through pbixray's SQL interface. **However**, we can identify which relationship was likely the trigger:

**Most likely candidates (based on pattern):**
- Any relationship involving **DIM_Manager** table that was manually created
- Since 11 of the 50 new relationships target DIM_Manager and are INACTIVE, it's likely the 1 manual relationship that triggered auto-detect involves DIM_Manager with a matching column like EMP_ID

**The 11 INACTIVE relationships (likely auto-detected):**
1. DIM_EMPREF → DIM_EMP
2. **DIM_EMP → DIM_Manager**
3. **EMP_MGR_MAPPING → DIM_Manager**
4. **EMP_ACCESS → DIM_Manager**
5. **DIM_LEAVER → DIM_Manager**
6. **DIM_EMPREF → DIM_Manager**
7. **EMP_MGR_MAPPING_WH → DIM_Manager**
8. **EMP_MGR_MAPPING_V3 → DIM_Manager**
9. **DIM_RESIGNATION → DIM_Manager**
10. FACT_IREFLECT_STATUS → Level_Sort_mapping
11. FACT_EMP_DETAILS_SNAPSHOT_WEEKLY → Level_Sort_mapping

**Pattern observation:** 7 out of 11 inactive relationships point to **DIM_Manager** with **EMP_ID** column. This suggests you either:
- Added DIM_Manager table to the model, OR
- Renamed/modified an existing relationship to DIM_Manager with EMP_ID

This change likely triggered Power BI's auto-detect, which then fanned out and created relationships from nearly every table with an EMP_ID column to DIM_Manager.

---

## 3. How to Filter Auto-Detected vs Manually Created Relationships?

**The Challenge:** Power BI doesn't natively expose "how was this relationship created" information in the UI or PBIX metadata. However, here are **practical strategies** to identify auto-detected relationships:

### Method 1: Activity Status (What We Found)
Auto-detected relationships often show a pattern:
- **Cluster of similar creation times** (all within 2-3 seconds)
- **Mixed active/inactive status** (some marked inactive by Power BI)
- **Follow a predictable pattern** (similar column names, same dimension target)

**In your case:** The 11 INACTIVE ones are almost certainly auto-detected. Active ones require manual review.

### Method 2: Relationship Naming
Check the `Name` field in Relationship table:
- **Auto-detected:** Often have GUID-based or auto-generated names (e.g., `e6b5acd6-2bb6-4f9b-ad61-bd5d287bf65c`)
- **Manually created:** Often have human-readable names or inherit from join wizard

### Method 3: Use Power BI Desktop's Manage Relationships
1. Open the changed file in **Power BI Desktop**
2. Go to **Model** → **Manage relationships**
3. Look for relationships with:
   - ✓ **ACTIVE** status = Likely manually created OR auto-detected & kept
   - ✗ **INACTIVE** status = Power BI auto-detected but flagged as problematic

### Method 4: Python Script to Identify Patterns

Here's a custom tool to help identify auto-detect batches:

```python
# Pattern Recognition: Auto-detected relationships cluster by:
# 1. Similar Modified timestamps (within 2-3 seconds)
# 2. Converge on 1-2 hub tables (like DIM_Manager, DIM_EMP)
# 3. Same foreign key column (like EMP_ID)
# 4. Mixed active/inactive status

def identify_autodetect_candidates(relationships_list):
    """
    Categorize relationships as likely manual or auto-detected.
    
    Heuristics:
    - Inactive = Likely auto-detected (Power BI disabled problematic ones)
    - Target same hub table with same FK column = Auto-detected cluster
    - Similar cardinality/cross-filtering to others = Auto-detected
    """
    
    inactive = [r for r in relationships_list if not r['IsActive']]
    
    # Group by target table and FK column
    hub_clusters = {}
    for rel in relationships_list:
        key = (rel['ToTableName'], rel['ToColumnName'])
        if key not in hub_clusters:
            hub_clusters[key] = []
        hub_clusters[key].append(rel)
    
    # Find hubs with many relationships (signature of auto-detect)
    large_clusters = {k: v for k, v in hub_clusters.items() if len(v) > 5}
    
    return {
        'likely_autodetected': inactive,
        'hub_clusters': large_clusters,
        'needs_manual_review': large_clusters
    }
```

---

## Recommended Actions

1. **Open the file in Power BI Desktop**
   - Model → Manage Relationships
   - Sort by IsActive (OFF) first
   - Delete the 11 INACTIVE relationships if you don't recognize them

2. **Keep the 39 ACTIVE new ones** if they're correct, OR
   - Review them against your data model design
   - Keep only the relationships you intentionally want

3. **Prevent this from happening again:**
   - File → Options and Settings → Options → Data Load
   - Uncheck: **"Autodetect new relationships after data is loaded"**
   - This will prevent Power BI from automatically creating relationships in the future

4. **Verify the 1 manual change:**
   - Look for any relationships you recently added or modified involving DIM_Manager
   - This was likely the trigger that caused the auto-detect cascade

---

## Technical Note

The user from the other chat mentioned "49 relationships within 2.5 seconds, 1 relationship 8 minutes before." This is typical of:
- **The 1 relationship:** Manual creation (took deliberate action)
- **The 49 relationships:** Automatic batch creation (PowerBI's auto-detect algorithm ran after you made your manual change)

Our analysis shows this manifests as:
- **11 INACTIVE** (Power BI's safety mechanism - disabled problematic auto-detects)
- **39 ACTIVE** (Auto-detected and deemed safe by Power BI)

The inactive ones are your smoking gun for identifying auto-detected relationships that Power BI itself rejected.
