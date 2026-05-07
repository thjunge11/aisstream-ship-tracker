- Select Marine Tracking API
- Use Kafka to consume data from Marine Tracking API
- evaluate if additional code is needed to reduce load on producer side
- Use ksql to process and store data in a SQL database per ship to have historical data
- Use WebApp to 
    - visualize updates on a live map and show (consume via kafka connect directly from kafka topic)
    - historical data per ship (query against SQL database)
- Deploy everything containerbased on aws EC2 instance via Github Actions/Terraform with CI/CD pipelines
- Use Github Actions for maintenace operations on SQL database
- Use Github Actions to add/edit/remove ships (add to kafka and SQL database), (structure like, on maintenace table for ships, single table per ship...EDR)

Optional:
- Have an admin interface in the WebApp to interact with Github Actions (if possible)
- Add alerting/monitoring system for specific events (e.g., ship entering a specific area, speed exceeding a threshold, etc.) in Additional App Code  container in parallel to webapp
- Add additional stream/REST API (like weather info) to kafka 
- Use PySpark on consumer side to combine weather data with ship live/historical data
- Use MongoDB to store some of the data
- Add a feature to export historical data for a specific ship in CSV format from the WebApp
- Add user authentication to the WebApp to restrict access to certain features or data
- Push data from Kafka to some DataWarehouse and consume from there
- Use AWS Glue/Lamdas for addtional stuff
- Use step functions to orchestrate some of the processes
- Use DynamoDB to store some of the data

- THINK about target metrics. What do you wnat to achieve with this project? (e.g., real-time tracking, historical analysis, alerting, etc.)


ToDos:
- Research and select a suitable Marine Tracking API that provides the necessary data for ship tracking.
- Set up a Kafka cluster to consume data from the selected Marine Tracking API.

Further Eval:
- https://www.tomtom.com/products/traffic-apis/

Additional ToDos:
- Kafka exercises and project
- Terrafrom excercises 03_intro_to_iac, 4_terraform_with_actions
- Lebenslauf tool
- AWS Skillbuilder zur Prüfungsvorbereitung aws data engineer

