-- Generic test: fails when column_name (a lower bound) is greater than
-- max_column (the matching upper bound) and both are non-null.
-- Usage in schema.yml, on the lower-bound column:
--   tests:
--     - min_not_greater_than_max:
--         max_column: salary_max_usd

{% test min_not_greater_than_max(model, column_name, max_column) %}

select *
from {{ model }}
where {{ column_name }} is not null
  and {{ max_column }} is not null
  and {{ column_name }} > {{ max_column }}

{% endtest %}
