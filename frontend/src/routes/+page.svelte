<script>
	import { Content, Grid, Row, Column } from "carbon-components-svelte";
	import { BarChartSimple } from "@carbon/charts-svelte";
    let isLoading = true;

    // Set a timeout to simulate loading
    setTimeout(() => {
        isLoading = false;
    }, 5000);
</script>

<Content>
	<Grid>
		<Row>
			<Column>
				<h1>Dashboard</h1>
				<BarChartSimple
					data={[
						{ group: "Todo", value: 23 },
						{ group: "In-Progress", value: 4 },
						{ group: "Complete", value: 15 },
						{ group: "Blocked", value: 1 },
					]}
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
                            loading: isLoading
                        }
					}}
				/>
			</Column>
		</Row>
	</Grid>
</Content>
