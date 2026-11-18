---
date: {{date:YYYY-MM-DD}}
tags:
  - daily
---

## runs
```dataview
list
from "raw/runs"
where contains(file.tags, "run")
	and date
	and date.year = this.date.year
	and date.month = this.date.month
	and date.day = this.date.day
sort file.name asc
```

<!-- One section per source in MORNING_SOURCES (automation/settings.env); work-morning.sh fills them. Rename or drop sections to match. -->
## linear
> Open tickets assigned to me — check via MCP: my assigned issues, updated today

## helpscout
> Active cases — check via MCP: open conversations assigned to me

## slack
> Action items from threads — check via MCP: unread mentions, threads needing reply

## tickets
-
```dataview
table without id
	file.link as ticket,
	status,
	source,
	due
from "raw/tickets"
where contains(file.tags, "ticket")
	and !contains(list("done", "reported", "cancelled"), status)
	and due
	and (
		due.year < date(today).year
		or (due.year = date(today).year and due.month < date(today).month)
		or (due.year = date(today).year and due.month = date(today).month and due.day <= date(today).day)
	)
sort due asc
```

## notes
- 

## meetings


## due
```dataview
table without id
	file.link as note,
	status,
	priority,
	due,
	project
from !"raw/templates"
	and #task
where (contains(file.tags, "task") or contains(file.tags, "project"))
	and due
	and (
		due.year < date(today).year
		or (due.year = date(today).year and due.month < date(today).month)
		or (due.year = date(today).year and due.month = date(today).month and due.day <= date(today).day)
	)
	and status = "active"
sort priority asc
```

## log
```dataview
table without id
	file.link as note,
	file.folder as location
from !"raw/templates" and !"raw/daily"
where file.mday.year = this.date.year
	and file.mday.month = this.date.month
	and file.mday.day = this.date.day
	and file.path != this.file.path
sort file.folder asc
```
