SELECT
  esh.id,
  esh.enterprise_name,
  esh.holder_type,
  esh.holder_name,
  esh.shareholding_ratio,
  target_e.enterprise_id AS target_enterprise_id,
  holder_p.person_id AS holder_person_id,
  holder_e.enterprise_id AS holder_enterprise_id
FROM enterprise_shareholding esh
LEFT JOIN enterprise target_e
  ON target_e.enterprise_name = esh.enterprise_name
LEFT JOIN person holder_p
  ON esh.holder_type = 1
 AND holder_p.name = esh.holder_name
LEFT JOIN enterprise holder_e
  ON esh.holder_type = 2
 AND holder_e.enterprise_name = esh.holder_name
ORDER BY esh.id;
