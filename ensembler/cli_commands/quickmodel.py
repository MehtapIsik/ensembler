from ensembler.tools.quick_model import QuickModel

helpstring_header = """\
Model a single target with multiple templates. Various options for specifying target and templates.

Options:"""

helpstring_unique_options = [
    """\
  --targetid <targetid>                             e.g. "--targetid EGFR_HUMAN_D0\"""",

    """\
  --templateids <templateids>                       Define one or more comma-separated template IDs to work on (e.g. "--templateids KC1D_HUMAN_D0_4KB8_D,KC1D_HUMAN_D0_4AF3_A")""",

    """\
  --target_uniprot_entry_name <entry_name>          e.g. "--target_uniprot_entry_name EGFR_HUMAN\"""",

    """\
  --template_pdbids <pdbids>                        e.g. "--template_pdbids 4KB8,4AF3\"""",

    """\
  --template_chainids <chainids>                    e.g. "--chainids AD,,A\"""",

    """\
  --template_uniprot_query <query>                  e.g. "--template_uniprot_query 'domain:"Protein kinase" AND reviewed:yes'\"""",

    """\
  --template_seqid_cutoff <cutoff>                  e.g. "--template_seqid_cutoff 80\"""",

    """\
  --no-loopmodel                                    """,

    """\
  --package_for_fah                                 """,
]

helpstring_nonunique_options = [
    """\
  --uniprot_domain_regex <regex>                    e.g. "--uniprot_domain_regex '^Protein kinase(?!; inactive)(?!; truncated)'\"""",

    """\
  --nfahclones <nfahclones>                         e.g. "--nfahclones 3\"""",

    """\
  --structure_paths <structure_paths>                 e.g. "--structure_paths  /Users/partond/tmp/kinome-MSMSeeder/structures/pdb,/Users/partond/tmp/kinome-MSMSeeder/structures/sifts\"""",
]

helpstring = '\n\n'.join([helpstring_header, '\n\n'.join(helpstring_unique_options), '\n\n'.join(helpstring_nonunique_options)])
docopt_helpstring = '\n\n'.join(helpstring_unique_options)


def dispatch(args):
    if args['--templateids']:
        templateids = args['--templateids'].split(',')
    else:
        templateids = None

    if args['--template_pdbids']:
        pdbids = args['--template_pdbids'].split(',')
    else:
        pdbids = None

    if args['--template_chainids']:
        chainids_list = [list(substr) for substr in args['--template_chainids'].split(',')]
        if len(pdbids) != len(chainids_list):
            raise Exception('If specified, chainids must be of the same length as pdbids.')
        chainids_dict = {}
        for p in range(len(pdbids)):
            chainids_dict[pdbids[p]] = chainids_list[p]
    else:
        chainids_dict = None

    if args['--template_seqid_cutoff']:
        template_seqid_cutoff = float(args['--template_seqid_cutoff'])
    else:
        template_seqid_cutoff = None

    if args['--nfahclones']:
        nfahclones = int(args['--nfahclones'])
    else:
        nfahclones = None

    if args['--structure_paths']:
        structure_paths = args['--structure_paths'].split(',')
    else:
        structure_paths = None

    QuickModel(targetid=args['--targetid'], templateids=templateids, target_uniprot_entry_name=args['--target_uniprot_entry_name'], uniprot_domain_regex=args['--uniprot_domain_regex'], pdbids=pdbids, chainids=chainids_dict, template_uniprot_query=args['--template_uniprot_query'], template_seqid_cutoff=template_seqid_cutoff, loopmodel=not args['--no-loopmodel'], package_for_fah=args['--package_for_fah'], nfahclones=nfahclones, structure_dirs=structure_paths)