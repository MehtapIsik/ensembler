import ensembler
import ensembler.modeling

helpstring_header = """\
Generate models by mapping target sequences onto template structures, using Modeller
(salilab.org/modeller).

MPI-enabled.

Options:"""

helpstring_unique_options = [
    """\
  --write_modeller_restraints_file  Write Modeller restraints file (default: False)
                                    Note that this file can be relatively large, e.g. ~300KB per
                                    model for a protein kinase domain target""",

    """\
  --template_seqid_cutoff <cutoff>  Select only templates with sequence identity (percentage)
                                    greater than the given cutoff.""",
]

helpstring_nonunique_options = [
    """\
  --targetsfile <targetsfile>       File containing a list of target IDs to work on (newline-separated).
                                    Comment targets out with "#".""",

    """\
  --targets <target>                Define one or more target IDs to work on (comma-separated), e.g.
                                    "--targets ABL1_HUMAN_D0,SRC_HUMAN_D0" (default: all targets)""",

    """\
  --templates <template>            Define one or more template IDs to work on (comma-separated), e.g.
                                    "--templates ABL1_HUMAN_D0_1OPL_A" (default: all templates)""",

    """\
  --templatesfile <templatesfile>   File containing a list of template IDs to work on (newline-separated).
                                    Comment targets out with "#".""",

    """\
  -v --verbose                      """,
]

helpstring = '\n\n'.join([helpstring_header, '\n\n'.join(helpstring_unique_options), '\n\n'.join(helpstring_nonunique_options)])
docopt_helpstring = '\n\n'.join(helpstring_unique_options)


def dispatch(args):
    if args['--targetsfile']:
        with open(args['--targetsfile'], 'r') as targetsfile:
            targets = [line.strip() for line in targetsfile.readlines() if line[0] != '#']
    elif args['--targets']:
        targets = args['--targets'].split(',')
    else:
        targets = False

    if args['--templatesfile']:
        with open(args['--templatesfile'], 'r') as templatesfile:
            templates = [line.strip() for line in templatesfile.readlines() if line[0] != '#']
    elif args['--templates']:
        templates = args['--templates'].split(',')
    else:
        templates = False

    if args['--template_seqid_cutoff']:
        template_seqid_cutoff = float(args['--template_seqid_cutoff'])
    else:
        template_seqid_cutoff = False

    if args['--verbose']:
        loglevel = 'debug'
    else:
        loglevel = 'info'

    ensembler.modeling.build_models(
        process_only_these_targets=targets,
        process_only_these_templates=templates,
        template_seqid_cutoff=template_seqid_cutoff,
        write_modeller_restraints_file=args['--write_modeller_restraints_file'],
        loglevel=loglevel
    )