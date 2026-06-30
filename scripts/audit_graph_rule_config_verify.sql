SELECT
  ar.id,
  ar.rule_name,
  ar.rule_type,
  ar.rule_status,
  argc.id AS graph_config_id,
  argc.status AS graph_config_status
FROM audit_rule ar
LEFT JOIN audit_rule_graph_config argc ON argc.rule_id = ar.id
ORDER BY ar.id
LIMIT 50;
