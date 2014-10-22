import sys
import os
import re
import datetime
import subprocess
import logging
import tempfile
import shutil
import gzip
from collections import namedtuple
import traceback
import msmseeder.version
import Bio
import Bio.SeqIO
import Bio.pairwise2
import Bio.SubsMat.MatrixInfo
import mpi4py.MPI
import msmseeder
import modeller
import modeller.automodel
from msmseeder.core import get_targets_and_templates

comm = mpi4py.MPI.COMM_WORLD
rank = comm.rank
size = comm.size

logger = logging.getLogger('info')

TargetSetupData = namedtuple(
    'TargetSetupData',
    ['target_starttime', 'models_target_dir']
)


@msmseeder.utils.notify_when_done
def build_models(process_only_these_targets=None, process_only_these_templates=None, loglevel=None):
    """Uses the build_model method to build homology models for a given set of
    targets and templates.

    MPI-enabled.
    """
    msmseeder.utils.loglevel_setter(logger, loglevel)

    targets, templates = get_targets_and_templates()
    ntemplates = len(templates)

    for target in targets:
        if process_only_these_targets and target.id not in process_only_these_targets: continue

        target_setup_data = build_models_setup_target(target, comm, rank)
        for template_index in range(rank, ntemplates, size):
            template = templates[template_index]
            if process_only_these_templates and template.id not in process_only_these_templates: continue

            build_model(target, template, target_setup_data, loglevel=loglevel)

        comm.Barrier()

        if rank == 0:
            metadata = gen_build_models_metadata(target, target_setup_data)
            msmseeder.core.write_metadata(metadata, msmseeder_stage='build_models', target_id=target.id)


def get_modeller_version():
    """Hacky attempt to get Modeller version by regex searching the installation directory or README file.
    """
    modeller_version = get_modeller_version_from_install_path(modeller)
    if modeller_version is not None:
        return modeller_version

    modeller_version = get_modeller_version_from_readme(modeller)
    if modeller_version is not None:
        return modeller_version


def get_modeller_version_from_install_path(modeller_module):
    regex = re.compile('/modeller-[0-9.]{2,6}/')
    match = re.search(regex, modeller_module.__file__)
    if match is not None:
        version = match.group()[10:-1]
        return version


def get_modeller_version_from_readme(modeller_module):
    readme_file_path = os.path.join(os.path.dirname(modeller_module.__file__), '..', '..', 'README')
    if os.path.exists(readme_file_path):
        with open(readme_file_path) as readme_file:
            # try first 10 lines
            # example desired line:
            #      MODELLER 9.11, 2012/08/29, r8834
            for i in range(10):
                line = readme_file.readline().strip()
                regex = re.compile('MODELLER [0-9.]{2,6}')
                match = re.search(regex, line)
                if match is not None:
                    version = match.group()[9:]
                    return version


def build_models_setup_target(target, comm=None, rank=0):
    models_target_dir = os.path.join(msmseeder.core.default_project_dirnames.models, target.id)
    if rank == 0:
        target_starttime = datetime.datetime.utcnow()
        logger = logging.getLogger('info')
        logger.info(
            '========================================================================='
            'Working on target "%s"'
            '========================================================================='
            % target.id
        )
        if not os.path.exists(models_target_dir):
            os.mkdir(models_target_dir)

    if comm is not None:
        comm.Barrier()
    target_setup_data = TargetSetupData(
        target_starttime=target_starttime,
        models_target_dir=models_target_dir
    )
    return target_setup_data


def gen_build_models_metadata(target, target_setup_data):
    """
    Generate build_models metadata for a given target.
    :param target:
    :param target_setup_data:
    :return: metadata: dict
    """
    datestamp = msmseeder.core.get_utcnow_formatted()
    nsuccessful_models = subprocess.check_output(['find', target_setup_data.models_target_dir, '-name', 'model.pdb.gz']).count('\n')
    target_timedelta = datetime.datetime.utcnow() - target_setup_data.target_starttime
    modeller_version = get_modeller_version()
    metadata = {
        'build_models': {
            'target_id': target.id,
            'datestamp': datestamp,
            'timing': msmseeder.core.strf_timedelta(target_timedelta),
            'nsuccessful_models': nsuccessful_models,
            'python_version': sys.version.split('|')[0].strip(),
            'python_full_version': msmseeder.core.literal_str(sys.version),
            'msmseeder_version': msmseeder.version.short_version,
            'msmseeder_commit': msmseeder.version.git_revision,
            'modeller_version': modeller_version if modeller_version is not None else '',
            'biopython_version': Bio.__version__
        }
    }
    return metadata


def build_model(target, template, target_setup_data, loglevel=None):
    """Uses Modeller to build a homology model for a given target and
    template.

    Will not run Modeller if the output files already exist.

    Parameters
    ----------
    target : BioPython SeqRecord
    template : BioPython SeqRecord
        Must be a corresponding .pdb template file with the same ID in the
        templates/structures directory.
    """
    msmseeder.utils.loglevel_setter(logger, loglevel)

    template_structure_dir = os.path.abspath(msmseeder.core.default_project_dirnames.templates_structures)
    model_dir = os.path.abspath(os.path.join(target_setup_data.models_target_dir, template.id))
    if not os.path.exists(model_dir):
        os.mkdir(model_dir)
    aln_filepath = os.path.abspath(os.path.join(model_dir, 'alignment.pir'))
    seqid_filepath = os.path.abspath(os.path.join(model_dir, 'sequence-identity.txt'))
    model_pdbfilepath = os.path.abspath(os.path.join(model_dir, 'model.pdb.gz'))
    restraint_filepath = os.path.abspath(os.path.join(model_dir, 'restraints.rsr.gz'))
    modeling_log_filepath = os.path.abspath(os.path.join(model_dir, 'modeling-log.yaml'))

    current_dir = os.getcwd()

    if model_pdbfilepath[-7:] != '.pdb.gz':
        raise Exception('model_pdbfilepath (%s) should end in .pdb.gz' % model_pdbfilepath)
    model_pdbfilepath_uncompressed = model_pdbfilepath[:-3]

    # Skip model-building if files already exist.
    files_to_check = [model_pdbfilepath, seqid_filepath, aln_filepath, restraint_filepath]
    files_are_present = [os.path.exists(filename) for filename in files_to_check]
    if all(files_are_present):
        logger.debug("Output files already exist for target '%s' // template '%s'; files were not overwritten." % (target.id, template.id))
        return

    logger.info(
        '-------------------------------------------------------------------------'
        'Modelling "%s" => "%s"'
        '-------------------------------------------------------------------------'
        % (target.id, template.id)
    )

    # Conduct alignment
    matrix = Bio.SubsMat.MatrixInfo.gonnet
    gap_open = -10
    gap_extend = -0.5
    aln = Bio.pairwise2.align.globalds(str(target.seq), str(template.seq), matrix, gap_open, gap_extend)

    # Create temp dir for modelling, and chdir
    temp_dir = tempfile.mkdtemp()

    # Open log file
    log_data = {
        'mpi_rank': rank,
        'complete': False,
    }
    log_filepath = modeling_log_filepath
    log_file = msmseeder.core.LogFile(log_filepath)
    log_file.log(new_log_data=log_data)

    try:
        start = datetime.datetime.utcnow()
        os.chdir(temp_dir)

        # Write Modeller-format PIR alignment file
        tmp_aln_filename = 'aligned.pir'
        contents = "Target-template alignment by clustal omega\n"
        contents += ">P1;%s\n" % target.id
        contents += "sequence:%s:FIRST:@:LAST :@:::-1.00:-1.00\n" % target.id
        contents += aln[0][0] + '*\n'
        contents += ">P1;%s\n" % template.id
        contents += "structureX:%s:FIRST:@:LAST : :undefined:undefined:-1.00:-1.00\n" % template.id
        contents += aln[0][1] + '*\n'
        outfile = open('aligned.pir', 'w')
        outfile.write(contents)
        outfile.close()

        # Run Modeller
        modeller.log.none()
        env = modeller.environ()
        env.io.atom_files_directory = [template_structure_dir]

        a = modeller.automodel.allhmodel(
            env,
            alnfile=tmp_aln_filename,
            knowns=template.id,
            sequence=target.id
        )
        a.make()                            # do homology modeling

        tmp_model_pdbfilename = a.outputs[0]['name']
        target_model = modeller.model(env, file=tmp_model_pdbfilename)

        target_model.write(file=model_pdbfilepath_uncompressed)
        with open(model_pdbfilepath_uncompressed) as model_pdbfile:
            with gzip.open(model_pdbfilepath, 'w') as model_pdbfilegz:
                model_pdbfilegz.write(model_pdbfile.read())

        # Note that the uncompressed pdb file needs to be kept until after the clustering step has completed

        # Write sequence identity.
        with open(seqid_filepath, 'w') as seqid_file:
            seqid_file.write('%.1f\n' % target_model.seq_id)

        # Copy restraints.
        with open('%s.rsr' % target.id, 'r') as rsrfile:
            with gzip.open(restraint_filepath, 'wb') as rsrgzfile:
                rsrgzfile.write(rsrfile.read())

        if os.path.getsize(model_pdbfilepath) < 1:
            raise Exception, 'Output PDB file is empty.'

        end = datetime.datetime.utcnow()
        timing = msmseeder.core.strf_timedelta(end - start)
        log_data = {
            'complete': True,
            'timing': timing,
        }
        log_file.log(new_log_data=log_data)

    except Exception as e:
        trbk = traceback.format_exc()
        log_data = {
            'exception': e,
            'traceback': msmseeder.core.literal_str(trbk),
        }
        log_file.log(new_log_data=log_data)

    finally:
        shutil.move(tmp_aln_filename, aln_filepath)
        os.chdir(current_dir)
        shutil.rmtree(temp_dir)


def deprecated_build_model(target,
                template,
                template_structure_dir='templates/structures',
                aln_filepath='alignment.pir',
                seqid_filepath='sequence-identity.txt',
                model_pdbfilepath='model.pdb.gz',
                restraint_filepath='restraints.rsr.gz',
                modeling_log_filepath='modeling-log.yaml',
                rank=0,
                verbose=False):
    r'''Uses Modeller to build a homology model for a given target and
    template.

    Will not run Modeller if the output files already exist.

    Parameters
    ----------
    target : BioPython SeqRecord
    template : BioPython SeqRecord
        Must be a corresponding .pdb template file with the same ID in the
        templates/structures directory.
    '''
    # align target and template

    # templates_dir = os.path.abspath('templates')
    # models_dir = os.path.abspath('models')
    # models_target_dir = os.path.join(models_dir, target.id)
    # model_dir = os.path.join(models_target_dir, template.id)
    # aln_filename = os.path.join(model_dir, 'alignment.pir')
    # seqid_filename = os.path.join(model_dir, 'sequence-identity.txt')
    # model_pdbfilename = os.path.join(model_dir, 'model.pdb')
    # restraint_filename_gz = os.path.join(model_dir, 'restraints.rsr.gz')
    template_structure_dir = os.path.abspath(template_structure_dir)
    aln_filepath = os.path.abspath(aln_filepath)
    seqid_filepath = os.path.abspath(seqid_filepath)
    model_pdbfilepath = os.path.abspath(model_pdbfilepath)
    restraint_filepath = os.path.abspath(restraint_filepath)
    modeling_log_filepath = os.path.abspath(modeling_log_filepath)
    current_dir = os.getcwd() 

    if model_pdbfilepath[-7:] != '.pdb.gz':
        raise Exception, 'model_pdbfilepath (%s) should end in .pdb.gz' % model_pdbfilepath
    model_pdbfilepath_uncompressed = model_pdbfilepath[:-3]

    # Skip model-building if files already exist.
    files_to_check = [model_pdbfilepath, seqid_filepath, aln_filepath, restraint_filepath]
    files_are_present = [os.path.exists(filename) for filename in files_to_check]
    if all(files_are_present):
        if verbose: print "Output files already exist for target '%s' // template '%s'; files were not overwritten." % (target.id, template.id)
        return

    print "-------------------------------------------------------------------------"
    print "Modelling '%s' => '%s'" % (target.id, template.id)
    print "-------------------------------------------------------------------------"

    # Conduct alignment
    matrix = Bio.SubsMat.MatrixInfo.gonnet
    gap_open = -10
    gap_extend = -0.5
    aln = Bio.pairwise2.align.globalds(str(target.seq), str(template.seq), matrix, gap_open, gap_extend)

    # Create temp dir for modelling, and chdir
    temp_dir = tempfile.mkdtemp()

    # Open log file
    log_data = {
        'mpi_rank': rank,
        'complete': False,
    }
    log_filepath = modeling_log_filepath
    log_file = msmseeder.core.LogFile(log_filepath)
    log_file.log(new_log_data=log_data)

    try:
        start = datetime.datetime.utcnow()
        os.chdir(temp_dir)

        # Write Modeller-format PIR alignment file
        tmp_aln_filename = 'aligned.pir'
        contents = "Target-template alignment by clustal omega\n"
        contents += ">P1;%s\n" % target.id
        contents += "sequence:%s:FIRST:@:LAST :@:::-1.00:-1.00\n" % target.id
        contents += aln[0][0] + '*\n'
        contents += ">P1;%s\n" % template.id
        contents += "structureX:%s:FIRST:@:LAST : :undefined:undefined:-1.00:-1.00\n" % template.id
        contents += aln[0][1] + '*\n'
        outfile = open('aligned.pir', 'w')
        outfile.write(contents)
        outfile.close()

        # Run Modeller
        modeller.log.none()
        env = modeller.environ()
        env.io.atom_files_directory = [template_structure_dir]

        a = modeller.automodel.allhmodel(env,
                                         # file with template codes and target sequence
                                         alnfile  = tmp_aln_filename,
                                         # PDB codes of the template
                                         knowns   = template.id,
                                         # code of the target
                                         sequence = target.id)
        a.make()                            # do homology modeling

        tmp_model_pdbfilename = a.outputs[0]['name']
        target_model = modeller.model(env, file=tmp_model_pdbfilename)

        target_model.write(file=model_pdbfilepath_uncompressed)
        with open(model_pdbfilepath_uncompressed) as model_pdbfile:
            with gzip.open(model_pdbfilepath, 'w') as model_pdbfilegz:
                model_pdbfilegz.write(model_pdbfile.read())

        # Note that the uncompressed pdb file needs to be kept until after the clustering step has completed

        # Write sequence identity.
        with open(seqid_filepath, 'w') as seqid_file:
            seqid_file.write('%.1f\n' % target_model.seq_id)

        # Copy restraints.
        with open('%s.rsr' % target.id, 'r') as rsrfile:
            with gzip.open(restraint_filepath, 'wb') as rsrgzfile:
                rsrgzfile.write(rsrfile.read())

        # XXX if os.path.getsize(model_pdbfilepath) < 1:
        #     raise Exception, 'Output PDB file is empty.'

        end = datetime.datetime.utcnow()
        timing = msmseeder.core.strf_timedelta(end - start)
        log_data = {
            'complete': True,
            'timing': timing,
        }
        log_file.log(new_log_data=log_data)

        text  = "---------------------------------------------------------------------------------\n"
        text += 'Successfully modeled target %s on template %s.\n' % (target.id, template.id)
        text += "Sequence identity was %.1f%%.\n" % (target_model.seq_id)
        return text

    # XXX except:
    #     try:
    #         reject_file_path = os.path.join(models_target_dir, 'modelling-rejected.txt')
    #         with open(reject_file_path, 'w') as reject_file:
    #             trbk = traceback.format_exc()
    #             reject_file.write(trbk)
    #     except Exception as e:
    #         print e
    #         print traceback.format_exc()
    #
    finally:
        # Copy alignment            
        shutil.move(tmp_aln_filename, aln_filepath)
        # Move back to current dir
        os.chdir(current_dir)
        shutil.rmtree(temp_dir)

def sort_by_sequence_identity(process_only_these_targets=None, verbose=False):
    '''Compile sorted list of templates by sequence identity.
    Runs serially.
    '''
    import os
    import numpy
    import Bio.SeqIO
    import mpi4py.MPI
    comm = mpi4py.MPI.COMM_WORLD 
    rank = comm.rank

    if rank == 0:
        targets_dir = os.path.abspath("targets")
        templates_dir = os.path.abspath("templates")
        models_dir = os.path.abspath("models")

        targets_fasta_filename = os.path.join(targets_dir, 'targets.fa')
        targets = list( Bio.SeqIO.parse(targets_fasta_filename, 'fasta') )
        templates_fasta_filename = os.path.join(templates_dir, 'templates.fa')
        templates = list( Bio.SeqIO.parse(templates_fasta_filename, 'fasta') )

        # ========
        # Compile sorted list by sequence identity
        # ========

        for target in targets:
            
            # Process only specified targets if directed.
            if process_only_these_targets and (target.id not in process_only_these_targets): continue

            models_target_dir = os.path.join(models_dir, target.id)
            if not os.path.exists(models_target_dir): continue

            print "-------------------------------------------------------------------------"
            print "Compiling template sequence identities for target %s" % (target.id)
            print "-------------------------------------------------------------------------"

            # ========
            # Build a list of valid models
            # ========

            if verbose: print "Building list of valid models..."
            valid_templates = list()
            for template in templates:
                model_filename = os.path.join(models_target_dir, template.id, 'model.pdb.gz')
                if os.path.exists(model_filename):
                    valid_templates.append(template)

            nvalid = len(valid_templates)
            if verbose: print "%d valid models found" % nvalid

            # ========
            # Sort by sequence identity
            # ========

            if verbose: print "Sorting models in order of decreasing sequence identity..."
            seqids = numpy.zeros([nvalid], numpy.float32)
            for (template_index, template) in enumerate(valid_templates):
                model_seqid_filename = os.path.join(models_target_dir, template.id, 'sequence-identity.txt')
                with open(model_seqid_filename, 'r') as model_seqid_file:
                    firstline = model_seqid_file.readline().strip()
                seqid = float(firstline)
                seqids[template_index] = seqid
            sorted_seqids = numpy.argsort(-seqids)

            # ========
            # Write templates sorted by sequence identity
            # ========

            seq_ofilename = os.path.join(models_target_dir, 'sequence-identities.txt')
            with open(seq_ofilename, 'w') as seq_ofile:
                for index in sorted_seqids:
                    template = valid_templates[index]
                    identity = seqids[index]
                    seq_ofile.write('%-40s %6.1f\n' % (template.id, identity))

            # ========
            # Metadata
            # ========

            import sys
            import yaml
            import msmseeder
            import msmseeder.version
            datestamp = msmseeder.core.get_utcnow_formatted()

            meta_filepath = os.path.join(models_target_dir, 'meta.yaml')
            with open(meta_filepath) as meta_file:
                metadata = yaml.load(meta_file)

            metadata['sort_by_sequence_identity'] = {
                'target_id': target.id,
                'datestamp': datestamp,
                'python_version': sys.version.split('|')[0].strip(),
                'python_full_version': msmseeder.core.literal_str(sys.version),
                'msmseeder_version': msmseeder.version.short_version,
                'msmseeder_commit': msmseeder.version.git_revision,
                'biopython_version': Bio.__version__
            }

            metadata = msmseeder.core.ProjectMetadata(metadata)
            meta_filepath = os.path.join(models_target_dir, 'meta.yaml')
            metadata.write(meta_filepath)

    comm.Barrier()
    if rank == 0:
        print 'Done.'

def cluster_models(process_only_these_targets=None, verbose=False):
    '''Cluster models based on RMSD, and filter out non-unique models as
    determined by a given cutoff.

    Runs serially.
    '''
    import os
    import gzip
    import glob
    import Bio.SeqIO
    import mdtraj
    import mpi4py.MPI
    comm = mpi4py.MPI.COMM_WORLD 
    rank = comm.rank

    if rank == 0:
        targets_dir = os.path.abspath("targets")
        templates_dir = os.path.abspath("templates")
        models_dir = os.path.abspath("models")

        targets_fasta_filename = os.path.join(targets_dir, 'targets.fa')
        targets = list( Bio.SeqIO.parse(targets_fasta_filename, 'fasta') )
        templates_fasta_filename = os.path.join(templates_dir, 'templates.fa')
        templates = list( Bio.SeqIO.parse(templates_fasta_filename, 'fasta') )

        cutoff = 0.06 # Cutoff for RMSD clustering (nm)

        for target in targets:
            if process_only_these_targets and (target.id not in process_only_these_targets): continue

            models_target_dir = os.path.join(models_dir, target.id)
            if not os.path.exists(models_target_dir): continue

            # =============================
            # Construct a mdtraj trajectory containing all models
            # =============================

            print 'Building a list of valid models...'

            model_pdbfilenames = []
            valid_templateIDs = []
            for t, template in enumerate(templates):
                model_dir = os.path.join(models_target_dir, template.id)
                model_pdbfilename = os.path.join(model_dir, 'model.pdb')
                if not os.path.exists(model_pdbfilename):
                    model_pdbfilename_compressed = os.path.join(model_dir, 'model.pdb.gz')
                    if not os.path.exists(model_pdbfilename_compressed):
                        continue
                    else:
                        with gzip.open(model_pdbfilename_compressed) as model_pdbfile_compressed:
                            with open(model_pdbfilename, 'w') as model_pdbfile:
                                model_pdbfile.write(model_pdbfile_compressed.read())
                model_pdbfilenames.append(model_pdbfilename)
                valid_templateIDs.append(template.id)

            print 'Constructing a trajectory containing all valid models...'

            traj = mdtraj.load(model_pdbfilenames)

            # =============================
            # Clustering
            # =============================

            print 'Conducting RMSD-based clustering...'

            # Remove any existing unique_by_clustering files
            for f in glob.glob( models_target_dir+'/*_PK_*/unique_by_clustering' ):
                os.unlink(f)

            # Each template will be added to the list uniques if it is further than
            # 0.2 Angstroms (RMSD) from the nearest template.
            uniques=[]
            min_rmsd = []
            for (t, templateID) in enumerate(valid_templateIDs):
                model_dir = os.path.join(models_target_dir, templateID)

                # Add the first template to the list of uniques
                if t==0:
                    uniques.append(templateID)
                    with open( os.path.join(model_dir, 'unique_by_clustering'), 'w') as unique_file: pass
                    continue

                # Cluster using CA atoms
                CAatoms = [a.index for a in traj.topology.atoms if a.name == 'CA']
                rmsds = mdtraj.rmsd(traj[0:t], traj[t], atom_indices=CAatoms, parallel=False)
                min_rmsd.append( min(rmsds) )

                if min_rmsd[-1] < cutoff:
                    continue
                else:
                    uniques.append( templateID )
                    # Create a blank file to say this template was found to be unique
                    # by clustering
                    with open( os.path.join(model_dir, 'unique_by_clustering'), 'w') as unique_file: pass

            with open( os.path.join(models_target_dir, 'unique-models.txt'), 'w') as uniques_file:
                for u in uniques:
                    uniques_file.write(u+'\n')
                print '%d unique models (from original set of %d) using cutoff of %.3f nm' % (len(uniques), len(valid_templateIDs), cutoff)

            for template in templates:
                model_dir = os.path.join(models_target_dir, template.id)
                model_pdbfilename = os.path.join(model_dir, 'model.pdb')
                if os.path.exists(model_pdbfilename):
                    os.remove(model_pdbfilename)

            # ========
            # Metadata
            # ========

            import sys
            import yaml
            import msmseeder
            import msmseeder.version
            import mdtraj.version
            datestamp = msmseeder.core.get_utcnow_formatted()

            meta_filepath = os.path.join(models_target_dir, 'meta.yaml')
            with open(meta_filepath) as meta_file:
                metadata = yaml.load(meta_file)

            metadata['cluster_models'] = {
                'target_id': target.id,
                'datestamp': datestamp,
                'nunique_models': len(uniques),
                'python_version': sys.version.split('|')[0].strip(),
                'python_full_version': msmseeder.core.literal_str(sys.version),
                'msmseeder_version': msmseeder.version.short_version,
                'msmseeder_commit': msmseeder.version.git_revision,
                'biopython_version': Bio.__version__,
                'mdtraj_version': mdtraj.version.short_version,
                'mdtraj_commit': mdtraj.version.git_revision
            }

            metadata = msmseeder.core.ProjectMetadata(metadata)
            meta_filepath = os.path.join(models_target_dir, 'meta.yaml')
            metadata.write(meta_filepath)

    comm.Barrier()
    if rank == 0:
        print 'Done.'

