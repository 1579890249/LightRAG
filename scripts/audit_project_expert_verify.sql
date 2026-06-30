SELECT 'project_expert' AS table_name, count(*) AS row_count
FROM project_expert;

SELECT
  pe.project_id,
  prj.project_name,
  pe.person_id AS expert_id,
  expert.name AS expert_name,
  expert.address AS expert_address,
  bid.enterprise_id AS bidder_id,
  bidder.enterprise_name AS bidder_name,
  bidder_person.person_id AS bidder_person_id,
  bidder_person.name AS bidder_person_name,
  bidder_person.address AS bidder_person_address,
  bpr.role_type AS bidder_role
FROM project_expert pe
JOIN project prj ON prj.project_id = pe.project_id
JOIN person expert ON expert.person_id = pe.person_id
JOIN bid_record bid ON bid.project_id = pe.project_id
JOIN enterprise bidder ON bidder.enterprise_id = bid.enterprise_id
LEFT JOIN bid_person_role bpr
  ON bpr.bid_id = bid.bid_id
LEFT JOIN person bidder_person
  ON bidder_person.person_id = bpr.person_id
WHERE pe.status = 'active'
  AND (
    expert.address = bidder_person.address
    OR expert.address LIKE '%南山区科技园%'
    OR bidder_person.address LIKE '%南山区科技园%'
    OR expert.address LIKE '%天河区软件路18号%'
    OR bidder_person.address LIKE '%天河区软件路18号%'
  )
ORDER BY pe.project_id, expert.person_id, bid.rank
LIMIT 50;
