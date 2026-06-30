-- Seed audit rules used by deterministic graph/path and rule-risk checks.
-- Idempotent by rule_name: existing rules are updated, missing rules are inserted.

BEGIN;

WITH seed_rules (
  rule_name,
  rule_type,
  rule_basis,
  rule_status,
  remark
) AS (
  VALUES
    (
      '人员关联关系校验',
      '人际关系',
      '核查对象：招标人单位负责人、项目经办人、招标代理经办人；评标委员会全体专家；所有投标人的法定代表人、授权委托人、项目负责人、股权实际控制人。
判定条件：上述人员之间存在任意已登记人员关系，或通过同一企业任职、同一项目角色、投标角色、股权实际控制关系形成关联路径的，标记为违规风险线索。',
      '1',
      '招投标审计规则'
    ),
    (
      '股权交叉控股校验',
      '股权关系',
      '核查对象：同一标段或同一项目的全部投标人之间，以及投标人与招标人之间的股权关系。
判定条件：
（1）两家及以上投标人存在直接持股、间接持股、共同股东、同一自然人股东同时参股多家投标企业，或企业之间存在股权穿透路径的，标记为股权关联风险。
（2）投标人与招标人之间存在直接或间接股权关联、共同股东、自然人股东关联的，标记为高风险关联线索。',
      '1',
      '招投标审计规则'
    ),
    (
      '人员住址相似校验',
      '地址关联',
      '核查对象：招标人项目负责人、招标代理经办人、评标专家、各投标人法定代表人、授权委托代理人、项目负责人。
判定条件：
（1）多方人员身份证地址、常住登记地址或登记地址高度重合，或仅楼栋、房间号不同，登记为同一小区或同一楼栋的，标记为疑似地址关联风险。
（2）住址高度相似且同时存在亲属关系、任职关系或股权关联路径的，标记为高风险关联线索。',
      '1',
      '招投标审计规则'
    ),
    (
      '陪标行为存疑检查',
      '投标行为',
      '核查对象：当前项目的全部投标人，以投标人组合为单位。
判定条件：
（1）查询历史投标记录，若当前项目中的3家及以上投标人在近3年内以完全相同或高度相似的组合共同参与过2次及以上其他项目投标，标记为疑似陪标或围标行为。
（2）上述公司组合在历史项目中呈现固定角色模式，例如同一企业多次排名靠前或中标，其他企业多次陪跑，且报价或排名呈现规律性差异的，标记为高风险陪标线索。
（3）该批公司在不同项目中轮流排名靠前或轮流中标，且其他投标方报价长期略高于排名靠前方的，标记为高风险围标串标线索。',
      '1',
      '招投标审计规则'
    )
),
updated AS (
  UPDATE audit_rule target
  SET
    rule_type = seed.rule_type,
    rule_basis = seed.rule_basis,
    rule_status = seed.rule_status,
    remark = seed.remark
  FROM seed_rules seed
  WHERE target.rule_name = seed.rule_name
  RETURNING target.rule_name
)
INSERT INTO audit_rule (
  rule_name,
  rule_basis,
  rule_status,
  rule_type,
  remark
)
SELECT
  seed.rule_name,
  seed.rule_basis,
  seed.rule_status,
  seed.rule_type,
  seed.remark
FROM seed_rules seed
WHERE NOT EXISTS (
  SELECT 1
  FROM audit_rule existing
  WHERE existing.rule_name = seed.rule_name
);

COMMIT;
