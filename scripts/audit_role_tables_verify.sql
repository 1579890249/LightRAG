SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'project'
ORDER BY ordinal_position;

SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN ('project_person_role', 'bid_person_role')
ORDER BY table_name;

SELECT project_id, project_name, tender_org, tender_org_id
FROM project
ORDER BY project_id;

SELECT 'project_person_role' AS table_name, count(*) AS row_count
FROM project_person_role
UNION ALL
SELECT 'bid_person_role' AS table_name, count(*) AS row_count
FROM bid_person_role
ORDER BY table_name;

SELECT
  bpr.person_id AS bidder_person_id,
  bidder.name AS bidder_person_name,
  pr1.relation_type AS relation_1,
  colleague.person_id AS colleague_person_id,
  colleague.name AS colleague_person_name,
  pr2.relation_type AS relation_2,
  tender_person.person_id AS tender_person_id,
  tender_person.name AS tender_person_name,
  p.project_id,
  p.project_name,
  bpr.role_type AS bid_role_type,
  ppr.role_type AS tender_role_type,
  bid_enterprise.enterprise_name AS bid_enterprise_name,
  tender_enterprise.enterprise_name AS tender_enterprise_name
FROM bid_person_role bpr
JOIN person bidder ON bidder.person_id = bpr.person_id
JOIN person_relation pr1 ON (
  (pr1.person_id_1 = bpr.person_id AND pr1.relation_type IN ('同事', '曾同事'))
  OR (pr1.person_id_2 = bpr.person_id AND pr1.relation_type IN ('同事', '曾同事'))
)
JOIN person colleague ON colleague.person_id = CASE
  WHEN pr1.person_id_1 = bpr.person_id THEN pr1.person_id_2
  ELSE pr1.person_id_1
END
JOIN person_relation pr2 ON (
  (pr2.person_id_1 = colleague.person_id AND pr2.relation_type IN ('夫妻', '配偶'))
  OR (pr2.person_id_2 = colleague.person_id AND pr2.relation_type IN ('夫妻', '配偶'))
)
JOIN person tender_person ON tender_person.person_id = CASE
  WHEN pr2.person_id_1 = colleague.person_id THEN pr2.person_id_2
  ELSE pr2.person_id_1
END
JOIN project_person_role ppr
  ON ppr.project_id = bpr.project_id
 AND ppr.person_id = tender_person.person_id
JOIN project p ON p.project_id = bpr.project_id
LEFT JOIN enterprise bid_enterprise ON bid_enterprise.enterprise_id = bpr.enterprise_id
LEFT JOIN enterprise tender_enterprise ON tender_enterprise.enterprise_id = ppr.enterprise_id
WHERE bpr.project_id = 'PJT101'
ORDER BY bpr.bid_id, bpr.person_id, ppr.person_id;
