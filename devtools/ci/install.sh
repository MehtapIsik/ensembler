MINICONDA=Miniconda-latest-Linux-x86_64.sh
MINICONDA_MD5=$(curl -s http://repo.continuum.io/miniconda/ | grep -A3 $MINICONDA | sed -n '4p' | sed -n 's/ *<td>\(.*\)<\/td> */\1/p')
wget http://repo.continuum.io/miniconda/$MINICONDA
if [[ $MINICONDA_MD5 != $(md5sum $MINICONDA | cut -d ' ' -f 1) ]]; then
    echo "Miniconda MD5 mismatch"
    exit 1
fi
bash $MINICONDA -b
PIP_ARGS="-U"

export PATH=$HOME/miniconda/bin:$PATH

sudo apt-get update

conda update --yes conda
conda config --add channels http://conda.binstar.org/omnia
# conda config --add channels http://conda.binstar.org/mpi4py
source activate $python
conda install --yes conda-build jinja2

wget https://salilab.org/modeller/9.14/modeller-9.14.tar.gz
tar -xvf modeller-9.14.tar.gz

cd modeller-9.14
./Install << EOF


$modeller_key

EOF
cd ..