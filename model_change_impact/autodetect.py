"""
autodetect.py

Identify relationships that are likely auto-detected by Power BI's autodetect
feature vs manually created. This allows filtering them from impact analysis.

Auto-detection heuristics:
1. IsActive == False → 100% indicator (Power BI disabled problematic auto-detects)
2. Hub clustering → Multiple relationships to same (ToTable, ToColumn)
3. Uniform cardinality → All relationships in cluster have identical cardinality
"""


def detect_autodetected_relationships(relationships_list):
    """
    Classify each relationship as MANUAL, AUTO_DETECTED, or UNCERTAIN.
    
    Returns: dict mapping relationship key to detection_method string
    
    Args:
        relationships_list: List of relationship dicts from snapshot
        (each with: from_table, from_column, to_table, to_column, is_active, cardinality, ...)
    """
    detection_results = {}
    
    # Heuristic 1: Inactive relationships = 100% auto-detected
    # Power BI marks problematic auto-detected relationships as inactive
    for rel in relationships_list:
        key = (rel['from_table'], rel['from_column'], rel['to_table'], rel['to_column'])
        if not rel.get('is_active', True):
            detection_results[key] = 'AUTO_DETECTED'
    
    # Heuristic 2: Hub clustering
    # Group by target (to_table, to_column) to find hubs with many relationships
    hub_clusters = {}
    for rel in relationships_list:
        hub_key = (rel['to_table'], rel['to_column'])
        if hub_key not in hub_clusters:
            hub_clusters[hub_key] = []
        hub_clusters[hub_key].append(rel)
    
    # If a hub has 5+ relationships from different tables, they're likely auto-detected
    for hub_key, cluster_rels in hub_clusters.items():
        if len(cluster_rels) >= 5:  # Threshold for hub clustering
            for rel in cluster_rels:
                key = (rel['from_table'], rel['from_column'], rel['to_table'], rel['to_column'])
                # Only mark as auto-detected if not already marked as manual
                if key not in detection_results or detection_results[key] != 'MANUAL':
                    # Check if cardinality is uniform (another indicator)
                    cardinalities = {r.get('cardinality') for r in cluster_rels}
                    if len(cardinalities) == 1:  # All same cardinality = likely auto-detected
                        detection_results[key] = 'AUTO_DETECTED'
                    else:
                        detection_results[key] = 'UNCERTAIN'
    
    # Any relationship not yet classified = MANUAL (assumed deliberately created)
    for rel in relationships_list:
        key = (rel['from_table'], rel['from_column'], rel['to_table'], rel['to_column'])
        if key not in detection_results:
            detection_results[key] = 'MANUAL'
    
    return detection_results


def identify_autodetect_cluster(relationships_list, changed_only_keys):
    """
    Identify which added relationships belong to a single auto-detect batch.
    
    Args:
        relationships_list: All relationships in changed file
        changed_only_keys: Set of relationship keys that are NEW (not in baseline)
    
    Returns:
        dict {relationship_key: cluster_id or None} for grouping purposes
    """
    autodetect_clusters = {}
    cluster_counter = 0
    
    # Find hubs with multiple new relationships
    hub_clusters = {}
    for rel in relationships_list:
        key = (rel['from_table'], rel['from_column'], rel['to_table'], rel['to_column'])
        if key in changed_only_keys:
            hub_key = (rel['to_table'], rel['to_column'])
            if hub_key not in hub_clusters:
                hub_clusters[hub_key] = []
            hub_clusters[hub_key].append((key, rel))
    
    # Mark relationships in large hubs as belonging to same cluster
    for hub_key, cluster_rels in hub_clusters.items():
        if len(cluster_rels) >= 3:  # 3+ new rels to same hub = likely one batch
            cluster_counter += 1
            for key, rel in cluster_rels:
                autodetect_clusters[key] = cluster_counter
    
    return autodetect_clusters


def tag_relationships_with_detection(relationships_list, detection_method_dict):
    """
    Add 'detection_method' field to each relationship dict.
    
    Args:
        relationships_list: List of relationship dicts (will be modified in place)
        detection_method_dict: Output from detect_autodetected_relationships()
    """
    for rel in relationships_list:
        key = (rel['from_table'], rel['from_column'], rel['to_table'], rel['to_column'])
        rel['detection_method'] = detection_method_dict.get(key, 'UNKNOWN')
