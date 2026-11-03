# Operation reference

Find your client in the [service index](reference/services.md). Each operation
page includes its keyword-only signature, arguments, Output fields, modeled
errors, pagination and streaming behavior. Nested inputs and outputs link to
their own type pages.

Method and type docstrings link directly to these pages. For example, the
reference for `S3Client.get_object` explains the returned `GetObjectOutput` and
how to consume its `ByteStream`. In your editor, hover over the method or type
to find the link.

Native outputs use attributes such as `response.body`. Dictionary outputs use
keys such as `response["output"]`. The reference identifies each representation
and distinguishes optional input keys from nullable values. Output enums and
unions can contain unknown variants that are not valid request inputs.

The website follows development source and shows its source version in each
service reference. It can be ahead of your installed package.

## Examples and service requirements

Operation pages link to the checked service examples where available. Replace
their resource names and configure credentials before running them.

Static types do not validate permissions, resource existence, lengths, ranges or
conditional requirements. Model notes are excerpts; follow the service-guide
and pinned-model links for more detail.
