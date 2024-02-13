<script>
	import { Content, Grid, Row, Column } from "carbon-components-svelte";
	import { BarChartSimple } from "@carbon/charts-svelte";
	export let data;
	let tasks = data.data;
	// Make a dictionary to hold the counts of each status
	let status_counts = {};
	// Loop through the tasks and count the status
	tasks.forEach((task) => {
		if (status_counts[task.status]) {
			status_counts[task.status] += 1;
		} else {
			status_counts[task.status] = 1;
		}
	});
	// Create a new array of objects to hold the status and count
	let status_counts_array = [];
	Object.keys(status_counts).forEach((key) => {
		status_counts_array.push({ group: key, value: status_counts[key] });
	});

</script>

<Content>
	<Grid>
		<Row>
			<Column>
				<h1>Dashboard</h1>
				<BarChartSimple
					data={status_counts_array}
					options={{
						theme: "g100",
						title: "Tasks by Category",
						height: "400px",
						axes: {
							left: { mapsTo: "value" },
							bottom: { mapsTo: "group", scaleType: "labels" },
						},
                        data:
                        {
                            loading: false
                        }
					}}
				/>
			</Column>
		</Row>
	</Grid>
</Content>
