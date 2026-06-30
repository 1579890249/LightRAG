SELECT
  id,
  rule_name,
  rule_type,
  rule_status,
  length(rule_basis) AS basis_len,
  remark
FROM audit_rule
WHERE rule_name IN (
  '人员关联关系校验',
  '股权交叉控股校验',
  '人员住址相似校验',
  '陪标行为存疑检查'
)
ORDER BY id;
