FROM apache/airflow:2.10.5-python3.12

COPY requirements.txt /tmp/requirements.txt
# The constraints file keeps our extra packages compatible with the exact Airflow release.
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-2.10.5/constraints-3.12.txt"
