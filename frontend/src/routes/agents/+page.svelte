<script>
	import {
		Content,
		Grid,
		Row,
		Column,
	DataTable,
	Tag,
	CodeSnippet,
	Toolbar,
	ToolbarContent,
	ToolbarSearch,
	} from "carbon-components-svelte";
  export let data;
  let filteredRowIds = [];
  let columns = [
	{
	  key: "_id",
	  value: "ID",
	},
	{
		key: "hostname",
	value: "Hostname"
	},
	{
	  key: "status",
	  value: "Status",
	},
	{
	  key: "username",
	  value: "User",
	},
	{
	  key: "os",
	  value: "OS",}
  ];

  let rows = data.data;
//   Take the rows and add an id key to the object
  let keyed_rows = rows.map((row, index) => {
	  //   Convert any null values into a string
		Object.keys(row).forEach((key) => {
			if (row[key] === null) {
				row[key] = "N/A";
			}
		});
	return {
	  ...row,
	  id: index,
	};
  });

  const colorMap = {
	"pending": "gray",
	"ready": "green",
	"error": "red",
	"running": "purple",
  };

</script>
  
<Content>
	<Grid>
		<Row>
			<Column>
				<h1>Agents</h1>
			</Column>
		</Row>
		<br />
		<Row>
			<Column>
				{#if rows}
					<DataTable sortable rows={keyed_rows} headers={columns}>
						<Toolbar>
							<ToolbarContent>
							  <ToolbarSearch
								persistent
								value=""
								shouldFilterRows
								bind:filteredRowIds
							  />
							</ToolbarContent>
						  </Toolbar>
						<svelte:fragment slot="cell" let:row let:cell>
							{#if cell.key === "status"}
							  <Tag type={colorMap[cell.value]}>{cell.value}</Tag>
							{:else}
							  {cell.value}
							{/if}
						  </svelte:fragment>
					</DataTable>
				{:else}
					<p>Loading...</p>
				{/if}
			</Column>
		</Row>
	</Grid>
</Content>
