"""RemoteRadar Streamlit dashboard.

Lives outside ``src/remoteradar`` on purpose: the Streamlit Community Cloud
deploy installs only ``dashboard/requirements.txt``, so this package must not
import the pipeline package (which would drag Prefect, Great Expectations and
dbt into the deploy image).
"""
